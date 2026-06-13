import base64
from langchain_core.messages import HumanMessage
from src.llm import get_llm

def ocr_images(state):
    llm = get_llm()
    processed_files = state["processed_files"]
    results = []
    
    for item in processed_files:
        with open(item["image_path"], "rb") as image_file:
            base64_image = base64.b64encode(image_file.read()).decode('utf-8')
        
        message = HumanMessage(
            content=[
                {"type": "text", "text": "Extract text from this image. If there are tables, convert them to markdown tables. Each row should be separated by a horizontal line."},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{base64_image}"}},
            ],
        )
        response = llm.invoke([message])
        results.append({
            "image_path": item["image_path"],
            "markdown": response.content,
            "source_pdf": item["source_pdf"]
        })
    
    return {"ocr_results": results}
