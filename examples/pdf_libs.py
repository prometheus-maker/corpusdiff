"""Plain text of every page, one function per PDF library. Each returns {"pages": n, "text": ...}.

corpusdiff fetch pdfjs corpora/pdfjs
for lib in pymupdf pdfium pypdf pdfplumber pdfminer; do
  corpusdiff run examples/pdf_libs.py:${lib}_text corpora/pdfjs -o $lib.jsonl -t 60 --save-outputs outs/
done
"""


def pypdf_text(path):
    from pypdf import PdfReader

    r = PdfReader(path)
    t = [p.extract_text() or "" for p in r.pages]
    return {"pages": len(t), "text": "\f".join(t)}


def pdfminer_text(path):
    from pdfminer.high_level import extract_text
    from pdfminer.pdfpage import PDFPage

    with open(path, "rb") as fp:
        n = sum(1 for _ in PDFPage.get_pages(fp, check_extractable=False))
    return {"pages": n, "text": extract_text(path)}


def pdfplumber_text(path):
    import pdfplumber

    with pdfplumber.open(path) as pdf:
        t = [p.extract_text() or "" for p in pdf.pages]
    return {"pages": len(t), "text": "\f".join(t)}


def pdfium_text(path):
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(path)
    try:
        t = []
        for i in range(len(doc)):
            page = doc[i]
            tp = page.get_textpage()
            t.append(tp.get_text_bounded())
            tp.close()
            page.close()
        return {"pages": len(t), "text": "\f".join(t)}
    finally:
        doc.close()


def pymupdf_text(path):
    import pymupdf

    with pymupdf.open(path) as doc:
        t = [p.get_text() for p in doc]
    return {"pages": len(t), "text": "\f".join(t)}
