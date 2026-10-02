"""Convert the reviewed Russian engineering report to an embedded-font PDF."""
from pathlib import Path
import re
from html import escape
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, KeepTogether
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.pagesizes import A4

ROOT=Path("/home/user/workspace")
pdfmetrics.registerFont(TTFont("Noto","/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf"))
pdfmetrics.registerFont(TTFont("Noto-Bold","/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf"))
pdfmetrics.registerFontFamily("Noto",normal="Noto",bold="Noto-Bold",italic="Noto",boldItalic="Noto-Bold")
styles={
    "title":ParagraphStyle("Title",fontName="Noto-Bold",fontSize=20,leading=26,textColor=colors.HexColor("#142c36"),spaceAfter=14),
    "heading":ParagraphStyle("Heading",fontName="Noto-Bold",fontSize=12.5,leading=17,textColor=colors.HexColor("#142c36"),spaceBefore=10,spaceAfter=5,keepWithNext=True),
    "body":ParagraphStyle("Body",fontName="Noto",fontSize=9.5,leading=13,spaceAfter=6,textColor=colors.HexColor("#263941")),
    "bullet":ParagraphStyle("Bullet",fontName="Noto",fontSize=9.5,leading=13,spaceAfter=5,leftIndent=11,firstLineIndent=-8,textColor=colors.HexColor("#263941")),
}
story=[]
for line in (ROOT/"ESP-Product-State-2026-10-02-RU.md").read_text().splitlines():
    if not line.strip():continue
    kind="body"
    if line.startswith("# "):kind="title";line=line[2:]
    elif line.startswith("## "):kind="heading";line=line[3:]
    elif line.startswith("- "):kind="bullet";line="• "+line[2:]
    # This installed Noto font lacks U+2212; preserve visible numeric signs.
    text=re.sub(r"\*\*(.*?)\*\*",r"<b>\1</b>",escape(line.replace("\u2212", "-")))
    story.append(Paragraph(text,styles[kind]))
def footer(canvas,doc):
    canvas.setFont("Noto",8)
    canvas.setFillColor(colors.HexColor("#60727a"))
    canvas.drawString(45,25,"2 октября 2026 • Синтетическая демонстрация • Не для выпуска проекта")
    canvas.drawRightString(A4[0]-45,25,str(doc.page))
out=ROOT/"Отчёт_итерация_2026-10-02.pdf"
doc=SimpleDocTemplate(str(out),pagesize=A4,leftMargin=45,rightMargin=45,topMargin=40,bottomMargin=43,
    title="ЭЦН: состояние прототипа — 2 октября 2026",author="Perplexity Computer")
doc.build(story,onFirstPage=footer,onLaterPages=footer)
print(out)
