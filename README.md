# Personal Finance Tracker

An automated tool to extract transaction data from PDF statements using LLMs and store them locally.

## Features

- **PDF Processing**: Extracts text from financial PDFs.
- **OCR Integration**: Handles image-based PDFs.
- **LLM Parsing**: Uses OpenAI models to parse unstructured text into structured data.
- **Local Storage**: Stores processed transactions in a local DuckDB database.

## Setup

1. **Clone the repository**:
   ```bash
   git clone <repository-url>
   cd personal-finance
   ```

2. **Install dependencies**:
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

3. **Configure environment variables**:
   Create a `.env` file in the root directory:
   ```env
   OPENAI_API_KEY=your_api_key_here
   OPENAI_BASE_URL=https://api.openai.com/v1
   ```

## Usage

Run the main pipeline to process a folder of PDFs:

```bash
python src/main.py --input ./path/to/pdfs --output ./path/to/output
```

## Testing

Run the test suite using pytest:

```bash
pytest tests/
```
