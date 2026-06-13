import os
from langchain_openai import ChatOpenAI
from dotenv import load_dotenv

def get_llm(model: str = "gpt-4o"):
    """
    Returns a configured ChatOpenAI instance based on environment variables.
    """
    load_dotenv()
    return ChatOpenAI(
        model=model,
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        openai_api_url=os.getenv("OPENAI_BASE_URL")
    )
