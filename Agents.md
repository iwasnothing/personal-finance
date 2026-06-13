Use langgraph to implement 

Create a langgraph workflow cli, accepting command line arguments: input folder, output folder, dpi, 

It recursively iterate the input folder for pdf files, convert each page into png image file, for each image, use llm as ocr to convert the image into markdown file, if there is any table, convert into markdown table, each row should be separated by a horizontal line,
The given pdf and converted md are banking account and credit card statement, which list all the transaction in the month.  The next node is to use llm to extract transaction from the markdown table.  The transaction contains the type (either income or expenditure), amount, date, and the income or expenditure category.  Export and save all extracted transactions in the duckdb for future analysis.
