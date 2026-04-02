## Personal finance pipeline (PDF statements → transactions → recurring subscriptions)

### What this does
- Converts all PDFs under `bank-statements/` into Markdown using `markitdown`.
- Extracts transaction entities (date, description, category, type, amount) using **`langextract`** when available, otherwise falls back to a heuristic extractor.
- Builds a Pandas DataFrame and saves `transactions.csv`.
- Detects recurring expenditures that repeat every **1 / 2 / 3 months** (regular subscriptions), excluding **credit-card payments** to avoid double counting.
- Summarizes regular income vs regular subscriptions and writes a surplus/deficit estimate.

### Setup

```bash
cd "/Users/kahingleung/Downloads/personal-finance"
python -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
```

### Run

```bash
source .venv/bin/activate
python bank_pipeline.py --extractor auto
```

If you want to force the LLM-based extractor:

```bash
python bank_pipeline.py --extractor langextract --model-id gemini-2.5-flash
```

### Outputs
All outputs are written under `artifacts/`:
- `artifacts/markdown/`: one `.md` per PDF
- `artifacts/extractions/`: one `.transactions.json` per PDF
- `artifacts/transactions.csv`: all extracted transactions
- `artifacts/regular_subscriptions.csv`: recurring expenditures (1/2/3 month cadence)
- `artifacts/regular_income.csv`: recurring income (1/2/3 month cadence)
- `artifacts/regular_subscriptions_reduction_candidates.csv`: same as subscriptions, sorted to highlight likely reducible items
- `artifacts/summary.json`: headline counts + per-month estimates + surplus/deficit

