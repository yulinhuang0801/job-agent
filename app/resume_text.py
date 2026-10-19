"""Pull plain text out of an uploaded CV (PDF, Word .docx, or text)."""
import io
import re
import zipfile
from xml.etree import ElementTree

from pypdf import PdfReader

MAX_CHARS = 60000
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class ResumeError(Exception):
    pass


def _pdf(data):
    reader = PdfReader(io.BytesIO(data))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _docx(data):
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        root = ElementTree.fromstring(z.read("word/document.xml"))
    lines = []
    for para in root.iter(_W + "p"):
        text = "".join(t.text or "" for t in para.iter(_W + "t"))
        if text.strip():
            lines.append(text)
    return "\n".join(lines)


def extract_text(filename, data):
    name = (filename or "").lower()
    try:
        if name.endswith(".pdf"):
            text = _pdf(data)
        elif name.endswith(".docx"):
            text = _docx(data)
        elif name.endswith((".txt", ".md")):
            text = data.decode("utf-8", errors="replace")
        else:
            raise ResumeError("Upload a PDF, Word (.docx) or text file.")
    except ResumeError:
        raise
    except Exception:
        raise ResumeError("Couldn't read that file. Try saving it as a PDF or .docx again.")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) < 100:
        raise ResumeError("Couldn't find text in that file. If it's a scanned image, upload a Word file or a text-based PDF.")
    return text[:MAX_CHARS]
