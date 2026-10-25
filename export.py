"""Readable, escaped HTML and paginated PDF trace matrices."""
from datetime import datetime
from html import escape
from itertools import groupby
from pathlib import Path


HEADERS = ["Requirement", "Title / description", "Parents", "Children", "Code locations", "Coverage"]


def groups(rows):
    def key(row):
        return row["document"], row["section"], row["type"].casefold(), row["type"]

    for _, items in groupby(sorted(rows, key=key), key=key):
        items = list(items)
        first = items[0]
        label = (f"{first['document_name']} / {first['section_name']} [{first['section_prefix']}] / "
                 f"{first['type'] or 'Unspecified'}")
        yield label, items


def cells(row):
    coverage = []
    if not row["parents"] and not row["children"]:
        coverage.append("No requirement links")
    if not row["code"]:
        coverage.append("No code tags")
    return [row["id"], row["title"] + "\n" + row["description"],
            "\n".join(row["parents"]), "\n".join(row["children"]),
            "\n".join(row["code"]), "\n".join(coverage) or "Linked"]


def description(project_name, rows):
    return (f"{project_name} | {len(rows)} requirements | "
            f"Generated {datetime.now():%Y-%m-%d %H:%M}. "
            "Coverage describes missing links; some requirements intentionally have no code tags.")


def export_html(path, project_name, rows):
    headings = "".join(f"<th scope='col'>{escape(h)}</th>" for h in HEADERS)
    body = []
    for label, items in groups(rows):
        body.append(f"<tbody><tr class='group'><th colspan='{len(HEADERS)}' scope='rowgroup'>{escape(label)}</th></tr>")
        for row in items:
            content = "".join("<td>" + escape(value).replace("\n", "<br>") + "</td>" for value in cells(row))
            body.append("<tr>" + content + "</tr>")
        body.append("</tbody>")
    html = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Requirements trace matrix</title><style>
body{font:14px system-ui,sans-serif;margin:24px;color:#172b3a}h1{font-size:24px}
table{width:100%;border-collapse:collapse;table-layout:fixed}th,td{border:1px solid #bdcbd5;
padding:9px;text-align:left;vertical-align:top;overflow-wrap:anywhere}th{background:#e4eef5}
tr:nth-child(even){background:#f7fafc}td:last-child{color:#775018}
.group th{background:#d5e2ec;padding-top:12px}
@media print{@page{size:landscape;margin:12mm}body{margin:0;font-size:9px}thead{display:table-header-group}}
</style></head><body><h1>Requirements trace matrix</h1><p>"""
    html += escape(description(project_name, rows)) + "</p><table><thead><tr>" + headings
    html += "</tr></thead>" + "\n".join(body) + "</table></body></html>"
    Path(path).write_text(html, encoding="utf-8")


def export_pdf(path, project_name, rows):
    # Lazy imports keep the app and HTML export usable without ReportLab.
    try:
        import reportlab
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.platypus import LongTable, Paragraph, SimpleDocTemplate, Spacer
    except ImportError:
        raise ValueError("PDF export needs ReportLab. Run: python -m pip install -r requirements.txt") from None
    font = Path(reportlab.__file__).parent / "fonts" / "Vera.ttf"
    if "TraceFont" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("TraceFont", str(font)))
    style = ParagraphStyle("cell", fontName="TraceFont", fontSize=7, leading=10, wordWrap="CJK")
    def paragraph(text):
        return Paragraph(escape(text).replace("\n", "<br/>"), style)

    data = [[paragraph(h) for h in HEADERS]]
    group_styles = []
    for label, items in groups(rows):
        index = len(data)
        data.append([paragraph(label)] + [""] * (len(HEADERS) - 1))
        group_styles.extend([("SPAN", (0, index), (-1, index)),
                             ("BACKGROUND", (0, index), (-1, index), colors.HexColor("#d5e2ec"))])
        data += [[paragraph(value) for value in cells(row)] for row in items]
    width, height = landscape(A4)
    table = LongTable(data, colWidths=[(width - 60) * n for n in (.12, .32, .10, .10, .24, .12)],
                      repeatRows=1, splitInRow=1, hAlign="LEFT")
    table.setStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e4eef5")),
                    ("GRID", (0, 0), (-1, -1), .4, colors.HexColor("#bdcbd5")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f7fafc")])] + group_styles)

    def footer(canvas, doc):
        canvas.setFont("TraceFont", 8)
        canvas.drawRightString(width - 24, 12, f"Page {doc.page}")

    doc = SimpleDocTemplate(str(path), pagesize=(width, height), leftMargin=24, rightMargin=24,
                            topMargin=24, bottomMargin=24, title=f"{project_name} - Trace matrix")
    doc.build([paragraph("REQUIREMENTS TRACE MATRIX"), Spacer(1, 8),
               paragraph(description(project_name, rows)), Spacer(1, 12), table],
              onFirstPage=footer, onLaterPages=footer)
