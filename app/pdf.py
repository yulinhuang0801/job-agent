"""Render resumes and cover letters to PDF from structured data. No AI tokens are spent here."""
import html
import io
import json
import os
import re
from datetime import date
from functools import lru_cache

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from reportlab.platypus.flowables import HRFlowable

_CJK = re.compile(r"[\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uff00-\uffef]")
MARGIN = 0.6 * inch
WIDTH = LETTER[0] - 2 * MARGIN - 12  # the page frame adds 6pt padding per side

# TrueType fonts with Chinese glyphs, embedded into the PDF so it renders in any viewer.
# (regular file, index in .ttc, bold file, index). Set JOB_AGENT_CJK_FONT to use your own .ttf.
_CJK_FONTS = [
    (os.environ.get("JOB_AGENT_CJK_FONT", ""), 0, "", 0),
    ("/System/Library/Fonts/STHeiti Light.ttc", 0, "/System/Library/Fonts/STHeiti Medium.ttc", 0),  # macOS
    ("/System/Library/Fonts/Supplemental/Arial Unicode.ttf", 0, "", 0),  # macOS
    ("C:/Windows/Fonts/msyh.ttc", 0, "C:/Windows/Fonts/msyhbd.ttc", 0),  # Windows
    ("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc", 0, "", 0),  # Linux (fonts-wqy-microhei)
]


def _t(s):
    return html.escape(str(s or "").strip())


@lru_cache(maxsize=1)
def _cjk_fonts():
    for regular, ri, bold, bi in _CJK_FONTS:
        if not regular or not os.path.exists(regular):
            continue
        try:
            pdfmetrics.registerFont(TTFont("CJK", regular, subfontIndex=ri))
            if bold and os.path.exists(bold):
                pdfmetrics.registerFont(TTFont("CJK-Bold", bold, subfontIndex=bi))
                return "CJK", "CJK-Bold"
            return "CJK", "CJK"
        except Exception:  # unsupported font format; try the next one
            continue
    # Last resort: Adobe's built-in Chinese font. Not embedded, so some viewers show blanks.
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    return "STSong-Light", "STSong-Light"


def _styles(*data):
    """Use an embedded Chinese font when any text is Chinese; Helvetica otherwise."""
    if _CJK.search(json.dumps(data, ensure_ascii=False)):
        regular, bold = _cjk_fonts()
        wrap = "CJK"
    else:
        regular, bold, wrap = "Helvetica", "Helvetica-Bold", None
    body = ParagraphStyle("body", fontName=regular, fontSize=10, leading=13, wordWrap=wrap)
    return {
        "name": ParagraphStyle("name", parent=body, fontName=bold, fontSize=18, leading=22, alignment=TA_CENTER),
        "contact": ParagraphStyle("contact", parent=body, fontSize=9.5, alignment=TA_CENTER, textColor=colors.HexColor("#444444")),
        "section": ParagraphStyle("section", parent=body, fontName=bold, fontSize=11, leading=14, spaceBefore=8),
        "entry": ParagraphStyle("entry", parent=body, fontName=bold),
        "right": ParagraphStyle("right", parent=body, alignment=TA_RIGHT, textColor=colors.HexColor("#444444")),
        "sub": ParagraphStyle("sub", parent=body, fontName=regular, textColor=colors.HexColor("#333333")),
        "body": body,
        "bullet": ParagraphStyle("bullet", parent=body, leftIndent=12, bulletIndent=2, spaceBefore=1),
        "letter": ParagraphStyle("letter", parent=body, fontSize=10.5, leading=15, spaceAfter=9),
    }


def _contact_line(header):
    parts = [header.get("email"), header.get("phone"), header.get("location")]
    for link in header.get("links") or []:
        if isinstance(link, dict):
            parts.append(link.get("url") or link.get("label"))
        else:
            parts.append(link)
    return "  |  ".join(_t(p) for p in parts if p)


def _dates(item):
    start, end = _t(item.get("start")), _t(item.get("end"))
    if start and end:
        return f"{start} – {end}"
    return start or end


def _entry_row(left, right, st):
    table = Table([[Paragraph(left, st["entry"]), Paragraph(right, st["right"])]], colWidths=[WIDTH * 0.62, WIDTH * 0.38])
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
    ]))
    return table


def _section(title, st):
    return [Paragraph(_t(title).upper(), st["section"]),
            HRFlowable(width="100%", thickness=0.6, color=colors.HexColor("#888888"), spaceBefore=1, spaceAfter=3)]


def _bullets(items, st):
    return [Paragraph(_t(b), st["bullet"], bulletText="•") for b in items or [] if str(b).strip()]


def _header(header, st):
    out = [Paragraph(_t(header.get("name")) or "Your Name", st["name"])]
    contact = _contact_line(header)
    if contact:
        out.append(Paragraph(contact, st["contact"]))
    return out


def _build(story, title, author):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=LETTER, leftMargin=MARGIN, rightMargin=MARGIN,
                            topMargin=0.5 * inch, bottomMargin=0.5 * inch, title=title, author=author)
    doc.build(story)
    return buf.getvalue()


def render_resume(header, body):
    """header: name/email/phone/location/links. body: summary, experience, projects, education, skills."""
    st = _styles(header, body)
    story = _header(header, st)

    if body.get("summary"):
        story += _section("Summary", st)
        story.append(Paragraph(_t(body["summary"]), st["body"]))

    if body.get("experience"):
        story += _section("Experience", st)
        for job in body["experience"]:
            left = " — ".join(x for x in [_t(job.get("title")), _t(job.get("company"))] if x)
            right = " · ".join(x for x in [_t(job.get("location")), _dates(job)] if x)
            story.append(_entry_row(left, right, st))
            story += _bullets(job.get("bullets"), st)

    if body.get("projects"):
        story += _section("Projects", st)
        for proj in body["projects"]:
            tech = ", ".join(_t(x) for x in proj.get("tech") or [])
            left = _t(proj.get("name")) + (f' <font size="9">({tech})</font>' if tech else "")
            story.append(_entry_row(left, _t(proj.get("link")), st))
            story += _bullets(proj.get("bullets"), st)

    if body.get("education"):
        story += _section("Education", st)
        for edu in body["education"]:
            story.append(_entry_row(_t(edu.get("school")), _dates(edu), st))
            degree = ", ".join(x for x in [_t(edu.get("degree")), _t(edu.get("field"))] if x)
            if edu.get("gpa"):
                degree += f" · GPA {_t(edu['gpa'])}"
            if degree:
                story.append(Paragraph(degree, st["sub"]))
            story += _bullets(edu.get("highlights"), st)

    skills = [g for g in body.get("skills") or [] if g.get("items")]
    if skills:
        story += _section("Skills", st)
        for group in skills:
            items = ", ".join(_t(i) for i in group["items"])
            story.append(Paragraph(f"<b>{_t(group.get('label'))}:</b> {items}", st["body"]))

    return _build(story, f"{header.get('name') or 'Resume'} – Resume", header.get("name") or "")


def render_cover_letter(header, letter, company=""):
    st = _styles(header, letter)
    story = _header(header, st)
    story.append(Spacer(1, 18))
    story.append(Paragraph(date.today().strftime("%B %d, %Y"), st["letter"]))
    if company:
        story.append(Paragraph(_t(company), st["letter"]))
    story.append(Paragraph(_t(letter.get("greeting") or "Dear Hiring Manager,"), st["letter"]))
    for para in letter.get("paragraphs") or []:
        story.append(Paragraph(_t(para), st["letter"]))
    story.append(Paragraph(_t(letter.get("closing") or "Sincerely,"), st["letter"]))
    story.append(Paragraph(_t(header.get("name")), st["letter"]))
    return _build(story, f"{header.get('name') or ''} – Cover Letter", header.get("name") or "")
