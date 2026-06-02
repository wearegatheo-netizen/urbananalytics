from __future__ import annotations

import copy
import html
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph


SRC_DOCX = Path("seoul_growth_projects_report_final.docx")
TEMPLATE_HWPX = Path("blank_test.hwpx")
OUT_HWPX = Path("seoul_growth_projects_report_final.hwpx")

NS = {
    "hs": "http://www.hancom.co.kr/hwpml/2011/section",
    "hp": "http://www.hancom.co.kr/hwpml/2011/paragraph",
}
ET.register_namespace("hs", NS["hs"])
ET.register_namespace("hp", NS["hp"])


def iter_block_items(document):
    body = document.element.body
    for child in body.iterchildren():
        if child.tag == qn("w:p"):
            yield Paragraph(child, document)
        elif child.tag == qn("w:tbl"):
            yield Table(child, document)


def paragraph_text(paragraph: Paragraph) -> str:
    return " ".join(paragraph.text.split())


def table_lines(table: Table) -> list[str]:
    lines = ["[표]"]
    for row in table.rows:
        values = []
        for cell in row.cells:
            text = " ".join(cell.text.split())
            values.append(text)
        lines.append(" | ".join(values))
    lines.append("[/표]")
    return lines


def docx_to_lines(path: Path) -> list[str]:
    doc = Document(path)
    lines: list[str] = []
    for block in iter_block_items(doc):
        if isinstance(block, Paragraph):
            text = paragraph_text(block)
            if not text:
                continue
            style = block.style.name if block.style is not None else ""
            if style.startswith("Heading"):
                lines.append("")
                lines.append(text)
            else:
                lines.append(text)
        else:
            lines.append("")
            lines.extend(table_lines(block))
            lines.append("")
    return lines


def make_p(text: str, pid: int):
    hp = f"{{{NS['hp']}}}"
    p = ET.Element(
        hp + "p",
        {
            "id": str(pid),
            "paraPrIDRef": "0",
            "styleIDRef": "0",
            "pageBreak": "0",
            "columnBreak": "0",
            "merged": "0",
        },
    )
    run = ET.SubElement(p, hp + "run", {"charPrIDRef": "0"})
    t = ET.SubElement(run, hp + "t")
    t.text = text
    linesegarray = ET.SubElement(p, hp + "linesegarray")
    ET.SubElement(
        linesegarray,
        hp + "lineseg",
        {
            "textpos": "0",
            "vertpos": "0",
            "vertsize": "1000",
            "textheight": "1000",
            "baseline": "850",
            "spacing": "600",
            "horzpos": "0",
            "horzsize": "42520",
            "flags": "393216",
        },
    )
    return p


def build_section(template_section: bytes, lines: list[str]) -> bytes:
    root = ET.fromstring(template_section)
    first = root.find("hp:p", NS)
    if first is None:
        raise RuntimeError("Template section has no paragraph")
    for child in list(root):
        root.remove(child)
    root.append(copy.deepcopy(first))
    pid = 1000
    for line in lines:
        if line == "":
            line = " "
        root.append(make_p(line, pid))
        pid += 1
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def main():
    lines = docx_to_lines(SRC_DOCX)
    preview = "\r\n".join(line for line in lines if line.strip())
    with zipfile.ZipFile(TEMPLATE_HWPX, "r") as zin, zipfile.ZipFile(
        OUT_HWPX, "w", compression=zipfile.ZIP_DEFLATED
    ) as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            if info.filename == "Contents/section0.xml":
                data = build_section(data, lines)
            elif info.filename == "Preview/PrvText.txt":
                data = preview.encode("utf-8")
            zout.writestr(info, data)
    print(OUT_HWPX)


if __name__ == "__main__":
    main()
