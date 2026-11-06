"""Text of an Office file, one function per library. Each returns {"text": ...} (plus counts where cheap)."""


def markitdown_text(path):
    from markitdown import MarkItDown
    return {"text": MarkItDown(enable_plugins=False).convert(path).text_content}


def python_docx_text(path):
    import docx
    d = docx.Document(path)
    parts = [p.text for p in d.paragraphs]
    for t in d.tables:
        for row in t.rows:
            parts.append("\t".join(c.text for c in row.cells))
    return {"text": "\n".join(parts)}


def docx2txt_text(path):
    import docx2txt
    return {"text": docx2txt.process(path)}


def mammoth_text(path):
    import mammoth
    with open(path, "rb") as f:
        return {"text": mammoth.extract_raw_text(f).value}


def python_pptx_text(path):
    from pptx import Presentation
    prs = Presentation(path)
    out = []
    for slide in prs.slides:
        for shape in slide.shapes:
            if shape.has_text_frame:
                out.append(shape.text_frame.text)
    return {"slides": len(prs.slides), "text": "\n".join(out)}


def openpyxl_text(path):
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        out = []
        for ws in wb.worksheets:
            for row in ws.iter_rows(values_only=True):
                out.append("\t".join("" if v is None else str(v) for v in row))
        return {"sheets": len(wb.worksheets), "text": "\n".join(out)}
    finally:
        wb.close()
