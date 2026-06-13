import json
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field
from typing import List

class Transaction(BaseModel):
    date: str
    type: str = Field(description="income or expenditure")
    amount: float
    category: str

import json
import os
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field
from typing import List
from dotenv import load_dotenv

class Transaction(BaseModel):
    date: str
    type: str = Field(description="income or expenditure")
    amount: float
    category: str

def parse_transactions(state):
    load_dotenv()
    llm = ChatOpenAI(
        model="gpt-4o",
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        openai_api_url=os.getenv("OPENAI_BASE_URL")
    )
    ocr_results = state["ocr_results"]
    all_transactions = []
    
    for result in ocr_results:
        prompt = f"Extract transactions from the following markdown content. Return a JSON list of transactions.\n\n{result['markdown']}"
        structured_llm = llm.with_structured_output(List[Transaction])
        transactions = structured_llm.invoke(prompt)
        
        for tx in transactions:
            all_transactions.append((
                result["source_pdf"],
                tx.date,
                tx.type,
                tx.amount,
                tx.category
            ))
            
    return {"transactions": all_transactions}
