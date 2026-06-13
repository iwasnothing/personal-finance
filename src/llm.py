import os
from langchain_openai import ChatOpenAI
from dotenv import load_dotenv

load_dotenv()

def get_llm(model: str = os.getenv("OPENAI_MODEL", "gpt-4o")):
    """
    Returns a configured ChatOpenAI instance based on environment variables.
    """
    return ChatOpenAI(
        model=model,
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        openai_api_url=os.getenv("OPENAI_BASE_URL")
    )
