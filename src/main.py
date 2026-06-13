import click
import os
from langgraph.graph import StateGraph, END
from src.nodes.pdf_processor import process_pdfs
from src.nodes.ocr_extractor import ocr_images
from src.nodes.transaction_parser import parse_transactions
from src.db.manager import DatabaseManager
from typing import TypedDict, List, Dict
from dotenv import load_dotenv

class AppState(TypedDict):
    input_folder: str
    output_folder: str
    dpi: int
    processed_files: List[Dict]
    ocr_results: List[Dict]
    transactions: List[tuple]

def db_node(state):
    db = DatabaseManager()
    db.insert_transactions(state["transactions"])
    return state

@click.command()
@click.option('--input', required=True, help='Input folder containing PDFs')
@click.option('--output', required=True, help='Output folder for intermediate files')
@click.option('--dpi', default=300, help='DPI for PDF conversion')
def main(input, output, dpi):
    load_dotenv()
    
    workflow = StateGraph(AppState)
    
    workflow.add_node("pdf_processor", process_pdfs)
    workflow.add_node("ocr_extractor", ocr_images)
    workflow.add_node("transaction_parser", parse_transactions)
    workflow.add_node("db_loader", db_node)
    
    workflow.set_entry_point("pdf_processor")
    workflow.add_edge("pdf_processor", "ocr_extractor")
    workflow.add_edge("ocr_extractor", "transaction_parser")
    workflow.add_edge("transaction_parser", "db_loader")
    workflow.add_edge("db_loader", END)
    
    app = workflow.compile()
    
    initial_state = {
        "input_folder": input,
        "output_folder": output,
        "dpi": dpi
    }
    
    app.invoke(initial_state)
    print("Processing complete.")

if __name__ == "__main__":
    main()
