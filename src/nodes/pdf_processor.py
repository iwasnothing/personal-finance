import os
from pdf2image import convert_from_path

def process_pdfs(state):
    input_folder = state["input_folder"]
    output_folder = state["output_folder"]
    dpi = state.get("dpi", 300)
    
    if not os.path.exists(output_folder):
        os.makedirs(output_folder)
        
    processed_files = []
    for root, _, files in os.walk(input_folder):
        for file in files:
            if file.endswith(".pdf"):
                pdf_path = os.path.join(root, file)
                images = convert_from_path(pdf_path, dpi=dpi)
                for i, image in enumerate(images):
                    image_name = f"{os.path.splitext(file)[0]}_page{i}.png"
                    image_path = os.path.join(output_folder, image_name)
                    image.save(image_path, "PNG")
                    processed_files.append({"image_path": image_path, "source_pdf": file})
    
    return {"processed_files": processed_files}
