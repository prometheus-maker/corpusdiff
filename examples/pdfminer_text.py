"""Target for pdfminer.six: page count + text of one PDF.

corpusdiff run examples/pdfminer_text.py:pages_and_text corpora/pdfjs -o master.jsonl --path ../pdfminer.six
"""

from pdfminer.high_level import extract_text
from pdfminer.pdfpage import PDFPage


def pages_and_text(path):
    with open(path, "rb") as fp:
        pages = sum(1 for _ in PDFPage.get_pages(fp, check_extractable=False))
    return {"pages": pages, "text": extract_text(path)}
