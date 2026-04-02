from __future__ import annotations

import argparse
import dataclasses
import json
import math
import os
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Optional
import dotenv
import pandas as pd
import requests

dotenv.load_dotenv()

# Compatibility: many OpenAI-compatible stacks use OPENAI_API_BASE; OpenAI Python SDK prefers OPENAI_BASE_URL.
if os.getenv("OPENAI_API_BASE") and not os.getenv("OPENAI_BASE_URL"):
    os.environ["OPENAI_BASE_URL"] = os.environ["OPENAI_API_BASE"]


def _openai_list_models(base_url: str, api_key: str) -> list[str]:
    url = base_url.rstrip("/") + "/models"
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        r.raise_for_status()
        payload = r.json()
        data = payload.get("data", []) if isinstance(payload, dict) else []
        out: list[str] = []
        for item in data:
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                out.append(item["id"])
        return sorted(set(out))
    except Exception:
        return []


@dataclass(frozen=True)
class Transaction:
    statement_id: str
    source_path: str
    source_kind: str  # "account" | "credit_card" | "unknown"
    data_source: str  # "bank statement" | "credit card statement" | "unknown"
    date: date
    description: str
    category: str
    type: str  # "expenditure" | "income"
    amount: float  # positive number
    currency: Optional[str] = None


def _source_kind_from_path(p: Path) -> str:
    parts = " / ".join(p.parts).lower()
    if "credit card" in parts or "credit-card" in parts or "creditcard" in parts:
        return "credit_card"
    if "account" in parts:
        return "account"
    return "unknown"

def _data_source_from_kind(kind: str) -> str:
    if kind == "credit_card":
        return "credit card statement"
    if kind == "account":
        return "bank statement"
    return "unknown"


def _statement_id(p: Path) -> str:
    # Stable ID across runs; include parent folder for same-named PDFs.
    try:
        rel = p.relative_to(p.parents[2])
        return str(rel).replace(os.sep, "__")
    except Exception:
        return str(p).replace(os.sep, "__")


def _ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


def _load_extraction_progress(path: Path) -> dict[str, Any]:
    if not path.exists() or path.stat().st_size == 0:
        return {"version": 1, "files": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {"version": 1, "files": {}}
    if not isinstance(payload, dict):
        return {"version": 1, "files": {}}
    files = payload.get("files")
    if not isinstance(files, dict):
        files = {}
    return {"version": 1, "files": files}


def _payload_to_transactions(payload: dict[str, Any]) -> list[Transaction]:
    txns: list[Transaction] = []
    statement_id = str(payload.get("statement_id", "")).strip()
    source_path = str(payload.get("source_path", "")).strip()
    source_kind = str(payload.get("source_kind", "unknown")).strip() or "unknown"
    rows = payload.get("transactions", [])
    if not isinstance(rows, list):
        return txns
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            d = _parse_date_any(str(row.get("date", "")).strip())
            if not d:
                continue
            desc = _normalize_spaces(str(row.get("description", "")).strip())
            if not desc:
                continue
            amount = abs(float(row.get("amount", 0)))
        except Exception:
            continue
        txns.append(
            Transaction(
                statement_id=statement_id,
                source_path=source_path,
                source_kind=source_kind,
                data_source=str(row.get("data_source", "")).strip() or _data_source_from_kind(source_kind),
                date=d,
                description=desc,
                category=str(row.get("category", "")).strip() or "Uncategorized",
                type=str(row.get("type", "")).strip().lower() if str(row.get("type", "")).strip() else "expenditure",
                amount=amount,
                currency=row.get("currency", None),
            )
        )
    return txns


def _convert_pdf_to_md(pdf_path: Path) -> str:
    from markitdown import MarkItDown

    mid = MarkItDown()
    res = mid.convert(pdf_path)
    md = res.markdown
    if not isinstance(md, str) or not md.strip():
        raise RuntimeError(f"Empty markdown from markitdown for {pdf_path}")
    return md


def _normalize_spaces(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _clean_merchant_key(description: str) -> str:
    s = description.lower()
    s = re.sub(r"\b(ref|reference|txn|transaction|trace|auth|authorization)\b.*$", "", s)
    s = re.sub(r"[\d]{2,}", " ", s)  # remove long digit sequences
    s = re.sub(r"[^a-z\s&/.-]+", " ", s)
    s = _normalize_spaces(s)
    # prune trailing location codes that cause false splits
    s = re.sub(r"\b(hk|hong kong|hkg|china|cn|us|usa|sg|singapore)\b$", "", s).strip()
    return s[:80] if len(s) > 80 else s


def _infer_category(description: str) -> str:
    # Do not hardcode merchants. Category should come from langextract.
    # This is only a fallback for heuristic extraction.
    return "Uncategorized"

def _infer_type_from_text(description: str, *, source_kind: str) -> str:
    # Credit card statements: treat all rows as expenditure (payments are excluded elsewhere).
    if source_kind == "credit_card":
        return "expenditure"

    d = description.lower()
    # Bank statements often mark columns/rows with CR/DR or words.
    income_hints = [
        "credit",
        "cr",
        "salary",
        "payroll",
        "interest",
        "dividend",
        "refund",
        "rebate",
        "deposit",
        "received",
    ]
    expense_hints = [
        "debit",
        "dr",
        "withdrawal",
        "purchase",
        "payment",
        "fee",
        "charge",
        "transfer out",
    ]
    if any(h in d for h in income_hints) and not any(h in d for h in expense_hints):
        return "income"
    return "expenditure"


_DATE_PATTERNS = [
    # 2026-03-25, 2026/03/25, 2026.03.25
    (re.compile(r"\b(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})\b"), "%Y-%m-%d"),
    # 25-03-2026, 25/03/2026
    (re.compile(r"\b(\d{1,2})[-/](\d{1,2})[-/](20\d{2})\b"), "%d-%m-%Y"),
    # 25 Mar 2026
    (re.compile(r"\b(\d{1,2})\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)\w*\s+(20\d{2})\b", re.I), "%d %b %Y"),
]


def _parse_date_any(s: str) -> Optional[date]:
    ss = s.strip()
    for rx, fmt in _DATE_PATTERNS:
        m = rx.search(ss)
        if not m:
            continue
        try:
            if fmt == "%Y-%m-%d":
                y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
                return date(y, mo, d)
            if fmt == "%d-%m-%Y":
                d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
                return date(y, mo, d)
            if fmt == "%d %b %Y":
                dt = datetime.strptime(f"{m.group(1)} {m.group(2)[:3]} {m.group(3)}", "%d %b %Y")
                return dt.date()
        except Exception:
            return None
    return None


_AMOUNT_RX = re.compile(
    r"(?P<sign>[-−])?\s*(?P<ccy>HKD|USD|EUR|GBP|CNY|RMB|\$|HK\$)?\s*(?P<num>\d{1,3}(?:,\d{3})*(?:\.\d{2})?|\d+(?:\.\d{2})?)"
)


def _parse_amount_any(s: str) -> Optional[tuple[float, Optional[str]]]:
    # Prefer the last amount-like number in the line (balances earlier, amounts later varies; last is a good heuristic).
    matches = list(_AMOUNT_RX.finditer(s))
    if not matches:
        return None
    m = matches[-1]
    raw = m.group("num").replace(",", "")
    try:
        v = float(raw)
    except Exception:
        return None
    ccy = m.group("ccy")
    if ccy:
        ccy = ccy.replace("HK$", "HKD").replace("$", "USD")
    return v, ccy


def _parse_amounts_any(s: str) -> list[tuple[float, Optional[str]]]:
    out: list[tuple[float, Optional[str]]] = []
    for m in _AMOUNT_RX.finditer(s):
        raw = m.group("num").replace(",", "")
        try:
            v = float(raw)
        except Exception:
            continue
        ccy = m.group("ccy")
        if ccy:
            ccy = ccy.replace("HK$", "HKD").replace("$", "USD")
        out.append((v, ccy))
    return out


def _heuristic_extract_transactions(md: str, *, statement_id: str, source_path: str, source_kind: str) -> list[Transaction]:
    txns: list[Transaction] = []
    for line in md.splitlines():
        if len(line) > 300:
            continue
        d = _parse_date_any(line)
        if not d:
            continue
        amounts = _parse_amounts_any(line)
        if not amounts:
            continue
        # Prefer the last amount on the line; if 2+ amounts exist (e.g. debit/credit columns),
        # choose the last non-zero amount and infer type via text.
        amount, ccy = amounts[-1]
        if amount == 0 and len(amounts) >= 2:
            nz = [a for a in amounts if a[0] != 0]
            if nz:
                amount, ccy = nz[-1]

        # description: remove date and amount-ish tails
        desc = line
        desc = re.sub(r"\s+", " ", desc).strip()
        # remove date occurrence
        desc = re.sub(r"\b(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})\b", " ", desc)
        desc = re.sub(r"\b(\d{1,2})[-/](\d{1,2})[-/](20\d{2})\b", " ", desc)
        desc = re.sub(r"\b(\d{1,2})\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)\w*\s+(20\d{2})\b", " ", desc, flags=re.I)
        # remove currency/amount occurrences
        desc = re.sub(_AMOUNT_RX, " ", desc)
        desc = _normalize_spaces(desc)
        if len(desc) < 3:
            continue

        ttype = _infer_type_from_text(desc, source_kind=source_kind)
        cat = _infer_category(desc)

        txns.append(
            Transaction(
                statement_id=statement_id,
                source_path=source_path,
                source_kind=source_kind,
                data_source=_data_source_from_kind(source_kind),
                date=d,
                description=desc,
                category=cat,
                type=ttype,
                amount=abs(float(amount)),
                currency=ccy,
            )
        )
    return txns


def _langextract_extract_transactions(md: str, *, statement_id: str, source_path: str, source_kind: str, model_id: str) -> list[Transaction]:
    """
    Uses langextract (LLM-based) extraction. If langextract isn't installed/configured, caller should fall back.
    """
    import langextract as lx

    source_label = _data_source_from_kind(source_kind)
    if source_label == "unknown":
        raise RuntimeError(f"Cannot determine data_source for {source_path}")

    base_url = os.getenv("OPENAI_BASE_URL") or os.getenv("OPENAI_API_BASE")
    api_key = os.getenv("OPENAI_API_KEY") or ""
    if not base_url:
        raise RuntimeError(
            "langextract requires OPENAI_BASE_URL or OPENAI_API_BASE (OpenAI-compatible endpoint)."
        )
    if not api_key:
        raise RuntimeError("langextract requires OPENAI_API_KEY (OpenAI-compatible endpoint).")

    prompt = (
        "Extract transactions from the text. "
        "A transaction entity MUST have attributes: "
        "date (ISO yyyy-mm-dd), description, category (infer from description), "
        "type (expenditure or income), amount (number, positive), data_source "
        "(exactly one of: 'bank statement' or 'credit card statement'). "
        f"For this document, data_source='{source_label}'. "
        "Rules: "
        "- Credit card statements: transactions are expenditures (except refunds which are income). "
        "- Bank statements: income/expenditure are determined by the credit/debit column or wording; do not rely on negative signs. "
        "Only extract real line-item transactions. Exclude: balances, relationship balance notes, reward summaries, totals, headers/footers."
        "You must return ONLY a valid JSON String. Do not include any conversational text, explanations, or preambles. Your entire response must be strictly valid JSON."
        "If no entities are found, you MUST return exactly: {'extractions': []}"
    )

    # Few-shot examples to enforce schema.
    examples = [
        lx.data.ExampleData(
            text="2026-01-15 Spotify Pte Ltd - Subscription HKD 68.00",
            extractions=[
                lx.data.Extraction(
                    extraction_class="transaction",
                    extraction_text="2026-01-15 Spotify Pte Ltd - Subscription HKD 68.00",
                    attributes={
                        "date": "2026-01-15",
                        "description": "Spotify Pte Ltd - Subscription",
                        "category": "Entertainment",
                        "type": "expenditure",
                        "amount": 68.00,
                        "data_source": "credit card statement",
                    },
                )
            ],
        ),
        lx.data.ExampleData(
            text="25/02/2026 PAYROLL SALARY CREDIT HK$ 35,000.00",
            extractions=[
                lx.data.Extraction(
                    extraction_class="transaction",
                    extraction_text="25/02/2026 PAYROLL SALARY CREDIT HK$ 35,000.00",
                    attributes={
                        "date": "2026-02-25",
                        "description": "PAYROLL SALARY",
                        "category": "Income",
                        "type": "income",
                        "amount": 35000.00,
                        "data_source": "bank statement",
                    },
                )
            ],
        ),
    ]

    # OpenAI-compatible endpoints only (non-OpenAI model ids route via the OpenAI provider).
    try:
        import langextract.providers.openai  # noqa: F401
    except Exception as e:
        raise RuntimeError(
            "The langextract OpenAI provider isn't available in this Python environment. "
            "Fix by installing: pip install 'langextract[openai]' openai"
        ) from e

    from langextract import factory

    available = _openai_list_models(base_url, api_key)
    if available and model_id not in available:
        raise RuntimeError(
            f"Model {model_id!r} not found at {base_url}. "
            f"Available models (sample): {available[:20]}"
        )

    config = factory.ModelConfig(
        model_id=model_id,
        provider="openai",
        provider_kwargs={
            "api_key": api_key,
            "base_url": base_url,
        },
    )

    res = lx.extract(
        text_or_documents=md,
        prompt_description=prompt,
        examples=examples,
        model_id=model_id,
        config=config,
        max_char_buffer=4000,
        max_workers=1,
        batch_length=1,
        show_progress=True,  # show progress bar
        fence_output=True,
        use_schema_constraints=False,
    )

    txns: list[Transaction] = []
    for ex in getattr(res, "extractions", []) or []:
        if getattr(ex, "extraction_class", None) != "transaction":
            continue
        attrs = getattr(ex, "attributes", None) or {}
        d_raw = str(attrs.get("date", "")).strip()
        desc = str(attrs.get("description", "")).strip()
        cat = str(attrs.get("category", "")).strip() or "Uncategorized"
        ttype = str(attrs.get("type", "")).strip().lower()
        amt_raw = attrs.get("amount", None)
        data_source = str(attrs.get("data_source", "")).strip().lower()

        d = _parse_date_any(d_raw) if d_raw else None
        if not d:
            d = _parse_date_any(getattr(ex, "extraction_text", "") or "")
        if not d:
            continue

        try:
            amount = float(amt_raw)
        except Exception:
            parsed = _parse_amount_any(getattr(ex, "extraction_text", "") or "")
            if not parsed:
                continue
            amount = abs(parsed[0])

        if not desc:
            desc = _normalize_spaces(getattr(ex, "extraction_text", "") or "")
        if not desc:
            continue

        if ttype not in {"expenditure", "income"}:
            ttype = _infer_type_from_text(desc, source_kind=source_kind)

        if data_source not in {"bank statement", "credit card statement"}:
            data_source = source_label

        txns.append(
            Transaction(
                statement_id=statement_id,
                source_path=source_path,
                source_kind=source_kind,
                data_source=data_source,
                date=d,
                description=desc,
                category=cat or "Uncategorized",
                type=ttype,
                amount=abs(float(amount)),
                currency=None,
            )
        )
    return txns


def _is_credit_card_payment(description: str) -> bool:
    d = description.lower()
    # Broad patterns to avoid double counting.
    needles = [
        "credit card payment",
        "card payment",
        "payment - credit card",
        "payment to credit card",
        "cc payment",
        "autopay",
        "auto pay",
        "card settlement",
        "statement payment",
        "payment thank you",
        "payment - thank you",
    ]
    return any(n in d for n in needles)


def _month_index(dt: date) -> int:
    return dt.year * 12 + dt.month


def _approx_equal(a: float, b: float, rel: float = 0.02, abs_tol: float = 2.0) -> bool:
    return abs(a - b) <= max(abs_tol, rel * max(abs(a), abs(b), 1.0))


def _detect_recurring_subscriptions(df: pd.DataFrame) -> pd.DataFrame:
    """
    Identify expenditures that recur every 1/2/3 months (approx).
    Output one row per (merchant_key, amount_bucket) with inferred frequency.
    """
    work = df.copy()
    work = work[work["type"] == "expenditure"].copy()
    work = work[~work["description"].map(_is_credit_card_payment)].copy()
    work["merchant_key"] = work["description"].map(_clean_merchant_key)
    work["month_idx"] = work["date"].map(lambda d: _month_index(d))

    # Cluster by merchant_key first; within, attempt to bucket by amount similarity.
    groups: list[dict[str, Any]] = []
    for merchant_key, g in work.groupby("merchant_key"):
        if not merchant_key:
            continue
        g = g.sort_values("date")
        amounts = g["amount"].tolist()
        if len(amounts) < 3:
            continue

        # Simple greedy bucketing by amount similarity.
        buckets: list[list[int]] = []
        bucket_reprs: list[float] = []
        for idx, amt in enumerate(amounts):
            placed = False
            for bi, rep in enumerate(bucket_reprs):
                if _approx_equal(amt, rep):
                    buckets[bi].append(idx)
                    # update representative (running mean)
                    bucket_reprs[bi] = (rep * (len(buckets[bi]) - 1) + amt) / len(buckets[bi])
                    placed = True
                    break
            if not placed:
                buckets.append([idx])
                bucket_reprs.append(amt)

        for bi, idxs in enumerate(buckets):
            if len(idxs) < 3:
                continue
            sub = g.iloc[idxs].copy()
            months = sorted(sub["month_idx"].unique().tolist())
            if len(months) < 3:
                continue
            deltas = [months[i + 1] - months[i] for i in range(len(months) - 1)]
            if not deltas:
                continue
            # choose dominant delta among 1,2,3
            counts = {k: sum(1 for d in deltas if d == k) for k in (1, 2, 3)}
            dominant = max(counts, key=lambda k: counts[k])
            coverage = counts[dominant] / max(1, len(deltas))
            if dominant not in (1, 2, 3) or coverage < 0.6:
                continue

            groups.append(
                {
                    "merchant_key": merchant_key,
                    "category": sub["category"].mode().iloc[0] if not sub["category"].mode().empty else "Uncategorized",
                    "frequency_months": dominant,
                    "amount_estimate": float(sub["amount"].median()),
                    "currency": sub["currency"].mode().iloc[0] if "currency" in sub.columns and not sub["currency"].mode().empty else None,
                    "occurrences": int(len(sub)),
                    "first_date": sub["date"].min(),
                    "last_date": sub["date"].max(),
                    "examples": " | ".join(sub["description"].head(3).tolist()),
                    "source_kinds": ",".join(sorted(set(sub["source_kind"].tolist()))),
                }
            )

    out = pd.DataFrame(groups)
    if out.empty:
        return out
    out = out.sort_values(["frequency_months", "amount_estimate"], ascending=[True, False]).reset_index(drop=True)
    return out


def _regular_income_summary(df: pd.DataFrame) -> pd.DataFrame:
    inc = df[df["type"] == "income"].copy()
    if inc.empty:
        return pd.DataFrame(columns=["merchant_key", "amount_estimate", "frequency_months", "occurrences", "first_date", "last_date", "examples"])
    inc["merchant_key"] = inc["description"].map(_clean_merchant_key)
    inc["month_idx"] = inc["date"].map(lambda d: _month_index(d))

    rows: list[dict[str, Any]] = []
    for merchant_key, g in inc.groupby("merchant_key"):
        g = g.sort_values("date")
        if len(g) < 2:
            continue
        months = sorted(g["month_idx"].unique().tolist())
        if len(months) < 2:
            continue
        deltas = [months[i + 1] - months[i] for i in range(len(months) - 1)]
        counts = {k: sum(1 for d in deltas if d == k) for k in (1, 2, 3)}
        dominant = max(counts, key=lambda k: counts[k])
        coverage = counts[dominant] / max(1, len(deltas))
        if dominant in (1, 2, 3) and coverage >= 0.6 and len(months) >= 3:
            rows.append(
                {
                    "merchant_key": merchant_key,
                    "amount_estimate": float(g["amount"].median()),
                    "frequency_months": dominant,
                    "occurrences": int(len(g)),
                    "first_date": g["date"].min(),
                    "last_date": g["date"].max(),
                    "examples": " | ".join(g["description"].head(3).tolist()),
                }
            )
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    return out.sort_values(["frequency_months", "amount_estimate"], ascending=[True, False]).reset_index(drop=True)


def _recommend_reductions(subs: pd.DataFrame) -> pd.DataFrame:
    if subs.empty:
        return subs
    optional_cats = {"Entertainment", "Dining", "Shopping", "Subscriptions", "Uncategorized"}
    work = subs.copy()
    work["reduction_candidate"] = work["category"].map(lambda c: c in optional_cats)
    work = work.sort_values(["reduction_candidate", "amount_estimate"], ascending=[False, False]).reset_index(drop=True)
    return work


def main() -> int:
    ap = argparse.ArgumentParser(description="PDF->MD->transactions->CSV pipeline + recurring subscription analysis")
    ap.add_argument(
        "--input-dir",
        default=str(Path(__file__).parent / "bank-statements"),
        help="Folder containing statement subfolders and PDFs",
    )
    ap.add_argument(
        "--output-dir",
        default=str(Path(__file__).parent / "artifacts"),
        help="Where to write markdown, json, csv outputs",
    )
    ap.add_argument(
        "--model-id",
        default="",
        help="langextract model_id for the OpenAI-compatible endpoint",
    )
    ap.add_argument(
        "--extractor",
        choices=["langextract", "heuristic", "auto"],
        default="langextract",
        help="Which extractor to use for transactions",
    )
    args = ap.parse_args()

    if not args.model_id:
        args.model_id = "glm-4-7"

    input_dir = Path(args.input_dir).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    md_dir = output_dir / "markdown"
    json_dir = output_dir / "extractions"
    progress_path = output_dir / "extraction_progress.json"
    _ensure_dir(output_dir)
    _ensure_dir(md_dir)
    _ensure_dir(json_dir)
    progress = _load_extraction_progress(progress_path)
    progress_files = progress["files"]

    pdfs = sorted([p for p in input_dir.rglob("*.pdf") if p.is_file()])
    if not pdfs:
        print(f"No PDFs found under {input_dir}")
        return 2

    all_txns: list[Transaction] = []

    can_langextract = False
    if args.extractor in {"auto", "langextract"}:
        try:
            import langextract  # noqa: F401

            can_langextract = True
        except Exception:
            can_langextract = False

    for pdf in pdfs:
        sid = _statement_id(pdf)
        kind = _source_kind_from_path(pdf)
        md_out = md_dir / (sid + ".md")
        json_out = json_dir / (sid + ".transactions.json")
        status = progress_files.get(sid, {}) if isinstance(progress_files.get(sid, {}), dict) else {}

        if json_out.exists() and json_out.stat().st_size > 0:
            try:
                existing_payload = json.loads(json_out.read_text(encoding="utf-8", errors="replace"))
                used_existing = str(existing_payload.get("extractor_used", "")).strip()
                if args.extractor != "langextract" or used_existing == "langextract":
                    existing_txns = _payload_to_transactions(existing_payload)
                    all_txns.extend(existing_txns)
                    progress_files[sid] = {
                        **status,
                        "pdf_path": str(pdf),
                        "md_path": str(md_out),
                        "json_path": str(json_out),
                        "md_ready": bool(md_out.exists() and md_out.stat().st_size > 0),
                        "transactions_extracted": True,
                        "status": "complete",
                        "extractor_used": used_existing or status.get("extractor_used", ""),
                        "transaction_count": len(existing_txns),
                        "updated_at": datetime.now().isoformat(timespec="seconds"),
                    }
                    _atomic_write_json(progress_path, progress)
                    continue
            except Exception:
                pass

        md: str
        if md_out.exists() and md_out.stat().st_size > 0:
            md = md_out.read_text(encoding="utf-8", errors="replace")
        else:
            md = _convert_pdf_to_md(pdf)
            md_out.write_text(md, encoding="utf-8")
        progress_files[sid] = {
            **status,
            "pdf_path": str(pdf),
            "md_path": str(md_out),
            "json_path": str(json_out),
            "md_ready": True,
            "transactions_extracted": False,
            "status": "markdown_ready",
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        _atomic_write_json(progress_path, progress)

        txns: list[Transaction] = []
        used = "heuristic"
        if args.extractor == "langextract" and not can_langextract:
            raise RuntimeError("extractor=langextract requested but langextract is not installed/configured")

        if (args.extractor in {"auto", "langextract"}) and can_langextract:
            try:
                txns = _langextract_extract_transactions(
                    md,
                    statement_id=sid,
                    source_path=str(pdf),
                    source_kind=kind,
                    model_id=args.model_id,
                )
                used = "langextract"
            except Exception as e:
                if args.extractor == "langextract":
                    raise RuntimeError(f"langextract failed for {pdf}: {e}") from e
                txns = []

        if not txns and args.extractor != "langextract":
            txns = _heuristic_extract_transactions(
                md,
                statement_id=sid,
                source_path=str(pdf),
                source_kind=kind,
            )
            used = "heuristic"

        if args.extractor == "langextract" and used != "langextract":
            raise RuntimeError(f"extractor=langextract requested but did not use langextract for {pdf}")

        extraction_payload = {
            "statement_id": sid,
            "source_path": str(pdf),
            "source_kind": kind,
            "extractor_used": used,
            "transactions": [
                {
                    "date": t.date.isoformat(),
                    "description": t.description,
                    "category": t.category,
                    "type": t.type,
                    "amount": t.amount,
                    "currency": t.currency,
                    "data_source": t.data_source,
                }
                for t in txns
            ],
        }
        _atomic_write_json(
            json_out,
            extraction_payload,
        )
        progress_files[sid] = {
            **(progress_files.get(sid, {}) if isinstance(progress_files.get(sid, {}), dict) else {}),
            "pdf_path": str(pdf),
            "md_path": str(md_out),
            "json_path": str(json_out),
            "md_ready": True,
            "transactions_extracted": True,
            "status": "complete",
            "extractor_used": used,
            "transaction_count": len(txns),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        _atomic_write_json(progress_path, progress)

        all_txns.extend(txns)

    df = pd.DataFrame([dataclasses.asdict(t) for t in all_txns])
    if df.empty:
        print("No transactions extracted.")
        return 3

    df["date"] = pd.to_datetime(df["date"]).dt.date
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce")
    df = df.dropna(subset=["date", "description", "amount"]).copy()
    df["description"] = df["description"].map(_normalize_spaces)
    df["category"] = df["category"].fillna("Uncategorized")
    df["type"] = df["type"].map(lambda x: str(x).lower().strip())
    df.loc[~df["type"].isin(["expenditure", "income"]), "type"] = "expenditure"

    # Exclude CC payments from overall totals to avoid double count vs CC statement transactions.
    df["is_credit_card_payment"] = df["description"].map(_is_credit_card_payment)

    tx_csv = output_dir / "transactions.csv"
    df.sort_values(["date", "amount"], ascending=[True, False]).to_csv(tx_csv, index=False)

    subs = _detect_recurring_subscriptions(df)
    subs_csv = output_dir / "regular_subscriptions.csv"
    subs.to_csv(subs_csv, index=False)

    inc = _regular_income_summary(df)
    inc_csv = output_dir / "regular_income.csv"
    inc.to_csv(inc_csv, index=False)

    # Regular totals estimate: convert to per-month equivalent.
    def per_month(row: pd.Series) -> float:
        f = float(row.get("frequency_months", 1) or 1)
        return float(row.get("amount_estimate", 0.0) or 0.0) / max(1.0, f)

    subs_pm = float(subs.apply(per_month, axis=1).sum()) if not subs.empty else 0.0
    inc_pm = float(inc.apply(per_month, axis=1).sum()) if not inc.empty else 0.0
    delta_pm = inc_pm - subs_pm

    reductions = _recommend_reductions(subs)
    reductions_csv = output_dir / "regular_subscriptions_reduction_candidates.csv"
    reductions.to_csv(reductions_csv, index=False)

    summary = {
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "pdf_count": len(pdfs),
        "transaction_count": int(len(df)),
        "regular_subscription_count": int(len(subs)),
        "regular_income_count": int(len(inc)),
        "regular_income_per_month_estimate": inc_pm,
        "regular_subscriptions_per_month_estimate": subs_pm,
        "surplus_deficit_per_month_estimate": delta_pm,
        "notes": [
            "Per-month estimates are computed by dividing amount_estimate by frequency_months (1/2/3).",
            "Credit-card payment-like rows are excluded when detecting subscriptions and marked in transactions.csv.",
        ],
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Wrote {tx_csv}")
    print(f"Wrote {subs_csv}")
    print(f"Wrote {inc_csv}")
    print(f"Wrote {reductions_csv}")
    print(f"Wrote {output_dir / 'summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

