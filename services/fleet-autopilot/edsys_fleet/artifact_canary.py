from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree
from xml.sax.saxutils import escape


class ArtifactCanaryError(RuntimeError):
    """Raised when the challenge-bound Office artifact canary is invalid."""


_HEX = re.compile(r"^[0-9a-f]{16}$")
_COLOR = {
    "ink": "071426",
    "surface": "0C2038",
    "cyan": "20E3FF",
    "blue": "4C7DFF",
    "green": "4BE0A0",
    "white": "F7FBFF",
    "muted": "AFC4D9",
    "rule": "28435E",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_zip(path: Path, members: dict[str, str]) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(members):
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            archive.writestr(info, members[name].encode("utf-8"))
    path.chmod(0o600)


def _inline_cell(ref: str, value: str, style: int = 0) -> str:
    return (
        f'<c r="{ref}" s="{style}" t="inlineStr"><is><t xml:space="preserve">'
        f"{escape(value)}</t></is></c>"
    )


def _number_cell(ref: str, value: int | float, style: int = 0) -> str:
    return f'<c r="{ref}" s="{style}"><v>{value}</v></c>'


def _formula_cell(ref: str, formula: str, cached: int | float, style: int = 0) -> str:
    return (
        f'<c r="{ref}" s="{style}"><f>{escape(formula)}</f><v>{cached}</v></c>'
    )


def _xlsx_members(spec: dict[str, Any], challenge: str) -> dict[str, str]:
    metrics = spec["metrics"]
    metric_rows: list[str] = []
    for index, metric in enumerate(metrics, start=6):
        metric_rows.append(
            f'<row r="{index}" ht="25" customHeight="1">'
            f'{_inline_cell(f"A{index}", metric["label"], 3)}'
            f'{_number_cell(f"C{index}", metric["value"], 4)}'
            f'{_inline_cell(f"E{index}", metric["status"], 5)}'
            "</row>"
        )
    average = sum(item["value"] for item in metrics) / len(metrics)
    last_row = 5 + len(metrics)
    summary_row = last_row + 2
    dashboard = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetViews><sheetView workbookViewId="0" showGridLines="0"><pane ySplit="5" topLeftCell="A6" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>
  <sheetFormatPr defaultRowHeight="20"/>
  <cols><col min="1" max="1" width="30" customWidth="1"/><col min="2" max="2" width="3" customWidth="1"/><col min="3" max="3" width="14" customWidth="1"/><col min="4" max="4" width="3" customWidth="1"/><col min="5" max="5" width="20" customWidth="1"/><col min="6" max="6" width="4" customWidth="1"/></cols>
  <sheetData>
    <row r="1" ht="36" customHeight="1">{_inline_cell("A1", spec["title"], 1)}</row>
    <row r="2" ht="30" customHeight="1">{_inline_cell("A2", spec["headline"], 2)}</row>
    <row r="3" ht="20" customHeight="1">{_inline_cell("A3", f"Challenge {challenge}", 6)}</row>
    <row r="5" ht="24" customHeight="1">{_inline_cell("A5", "CAPABILITY", 7)}{_inline_cell("C5", "SCORE", 7)}{_inline_cell("E5", "STATE", 7)}</row>
    {''.join(metric_rows)}
    <row r="{summary_row}" ht="28" customHeight="1">{_inline_cell(f"A{summary_row}", "AVERAGE", 7)}{_formula_cell(f"C{summary_row}", f"AVERAGE(C6:C{last_row})", round(average, 2), 8)}{_inline_cell(f"E{summary_row}", "FORMULA VERIFIED", 5)}</row>
  </sheetData>
  <mergeCells count="3"><mergeCell ref="A1:F1"/><mergeCell ref="A2:F2"/><mergeCell ref="A3:F3"/></mergeCells>
  <autoFilter ref="A5:E{last_row}"/>
  <pageMargins left="0.35" right="0.35" top="0.45" bottom="0.45" header="0.2" footer="0.2"/>
  <pageSetup orientation="landscape" fitToWidth="1" fitToHeight="1"/>
</worksheet>'''
    assumptions_rows = [
        '<row r="1" ht="34" customHeight="1">'
        + _inline_cell("A1", "Benchmark contract", 1)
        + "</row>",
        '<row r="3">'
        + _inline_cell("A3", "Control", 7)
        + _inline_cell("B3", "Expected", 7)
        + "</row>",
        '<row r="4">'
        + _inline_cell("A4", "Model", 3)
        + _inline_cell("B4", "gpt-5.6-sol", 3)
        + "</row>",
        '<row r="5">'
        + _inline_cell("A5", "Reasoning", 3)
        + _inline_cell("B5", "Ultra", 3)
        + "</row>",
        '<row r="6">'
        + _inline_cell("A6", "Service tier", 3)
        + _inline_cell("B6", "Priority", 3)
        + "</row>",
        '<row r="7">'
        + _inline_cell("A7", "Challenge hash", 3)
        + _inline_cell("B7", hashlib.sha256(challenge.encode()).hexdigest()[:16], 6)
        + "</row>",
    ]
    assumptions = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetViews><sheetView workbookViewId="0" showGridLines="0"/></sheetViews>
  <sheetFormatPr defaultRowHeight="20"/>
  <cols><col min="1" max="1" width="24" customWidth="1"/><col min="2" max="2" width="28" customWidth="1"/></cols>
  <sheetData>{''.join(assumptions_rows)}</sheetData>
  <mergeCells count="1"><mergeCell ref="A1:B1"/></mergeCells>
  <pageMargins left="0.5" right="0.5" top="0.5" bottom="0.5" header="0.2" footer="0.2"/>
</worksheet>'''
    styles = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <numFmts count="1"><numFmt numFmtId="164" formatCode="0.0&quot; / 100&quot;"/></numFmts>
  <fonts count="4">
    <font><sz val="11"/><color rgb="FF{_COLOR['ink']}"/><name val="Aptos"/></font>
    <font><b/><sz val="24"/><color rgb="FF{_COLOR['white']}"/><name val="Aptos Display"/></font>
    <font><b/><sz val="15"/><color rgb="FF{_COLOR['cyan']}"/><name val="Aptos Display"/></font>
    <font><b/><sz val="10"/><color rgb="FF{_COLOR['muted']}"/><name val="Aptos"/></font>
  </fonts>
  <fills count="5"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF{_COLOR['ink']}"/><bgColor indexed="64"/></patternFill></fill><fill><patternFill patternType="solid"><fgColor rgb="FF{_COLOR['surface']}"/><bgColor indexed="64"/></patternFill></fill><fill><patternFill patternType="solid"><fgColor rgb="FF123F45"/><bgColor indexed="64"/></patternFill></fill></fills>
  <borders count="2"><border><left/><right/><top/><bottom/><diagonal/></border><border><left/><right/><top/><bottom style="thin"><color rgb="FF{_COLOR['rule']}"/></bottom><diagonal/></border></borders>
  <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
  <cellXfs count="9">
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
    <xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"><alignment vertical="center"/></xf>
    <xf numFmtId="0" fontId="2" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"><alignment vertical="center"/></xf>
    <xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1"><alignment vertical="center"/></xf>
    <xf numFmtId="164" fontId="2" fillId="0" borderId="1" xfId="0" applyNumberFormat="1" applyFont="1" applyBorder="1"><alignment horizontal="center" vertical="center"/></xf>
    <xf numFmtId="0" fontId="3" fillId="4" borderId="0" xfId="0" applyFont="1" applyFill="1"><alignment horizontal="center" vertical="center"/></xf>
    <xf numFmtId="0" fontId="3" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"><alignment vertical="center"/></xf>
    <xf numFmtId="0" fontId="3" fillId="3" borderId="0" xfId="0" applyFont="1" applyFill="1"><alignment vertical="center"/></xf>
    <xf numFmtId="164" fontId="2" fillId="3" borderId="0" xfId="0" applyNumberFormat="1" applyFont="1" applyFill="1"><alignment horizontal="center" vertical="center"/></xf>
  </cellXfs>
  <cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>'''
    theme = _office_theme()
    return {
        "[Content_Types].xml": '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/><Override PartName="/xl/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/><Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/><Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/></Types>''',
        "_rels/.rels": '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/><Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/></Relationships>''',
        "docProps/core.xml": _core_properties(spec["title"]),
        "docProps/app.xml": '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"><Application>EdSys Fleet Autopilot</Application><AppVersion>2.0</AppVersion></Properties>''',
        "xl/workbook.xml": '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><bookViews><workbookView activeTab="0"/></bookViews><sheets><sheet name="Capability Dashboard" sheetId="1" r:id="rId1"/><sheet name="Contract" sheetId="2" r:id="rId2"/></sheets><calcPr calcId="191029" fullCalcOnLoad="1" forceFullCalc="1"/></workbook>''',
        "xl/_rels/workbook.xml.rels": '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/><Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/><Relationship Id="rId4" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme" Target="theme/theme1.xml"/></Relationships>''',
        "xl/styles.xml": styles,
        "xl/theme/theme1.xml": theme,
        "xl/worksheets/sheet1.xml": dashboard,
        "xl/worksheets/sheet2.xml": assumptions,
    }


def _core_properties(title: str) -> str:
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><dc:title>{escape(title)}</dc:title><dc:creator>EdSys Fleet Autopilot</dc:creator><cp:lastModifiedBy>EdSys Fleet Autopilot</cp:lastModifiedBy><dcterms:created xsi:type="dcterms:W3CDTF">2026-01-01T00:00:00Z</dcterms:created><dcterms:modified xsi:type="dcterms:W3CDTF">2026-01-01T00:00:00Z</dcterms:modified></cp:coreProperties>'''


def _office_theme() -> str:
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="EdSys"><a:themeElements><a:clrScheme name="EdSys"><a:dk1><a:srgbClr val="{_COLOR['ink']}"/></a:dk1><a:lt1><a:srgbClr val="{_COLOR['white']}"/></a:lt1><a:dk2><a:srgbClr val="{_COLOR['surface']}"/></a:dk2><a:lt2><a:srgbClr val="E8F3FA"/></a:lt2><a:accent1><a:srgbClr val="{_COLOR['cyan']}"/></a:accent1><a:accent2><a:srgbClr val="{_COLOR['blue']}"/></a:accent2><a:accent3><a:srgbClr val="{_COLOR['green']}"/></a:accent3><a:accent4><a:srgbClr val="FFB454"/></a:accent4><a:accent5><a:srgbClr val="D77DFF"/></a:accent5><a:accent6><a:srgbClr val="FF6B7A"/></a:accent6><a:hlink><a:srgbClr val="4C7DFF"/></a:hlink><a:folHlink><a:srgbClr val="8F65C2"/></a:folHlink></a:clrScheme><a:fontScheme name="EdSys"><a:majorFont><a:latin typeface="Aptos Display"/><a:ea typeface=""/><a:cs typeface=""/></a:majorFont><a:minorFont><a:latin typeface="Aptos"/><a:ea typeface=""/><a:cs typeface=""/></a:minorFont></a:fontScheme><a:fmtScheme name="EdSys"><a:fillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:gradFill rotWithShape="1"><a:gsLst><a:gs pos="0"><a:schemeClr val="phClr"><a:tint val="50000"/><a:satMod val="300000"/></a:schemeClr></a:gs><a:gs pos="100000"><a:schemeClr val="phClr"><a:shade val="50000"/><a:satMod val="200000"/></a:schemeClr></a:gs></a:gsLst><a:lin ang="16200000" scaled="1"/></a:gradFill><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:fillStyleLst><a:lnStyleLst><a:ln w="6350" cap="flat" cmpd="sng" algn="ctr"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:prstDash val="solid"/></a:ln><a:ln w="12700" cap="flat" cmpd="sng" algn="ctr"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:prstDash val="solid"/></a:ln><a:ln w="19050" cap="flat" cmpd="sng" algn="ctr"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:prstDash val="solid"/></a:ln></a:lnStyleLst><a:effectStyleLst><a:effectStyle><a:effectLst/></a:effectStyle><a:effectStyle><a:effectLst/></a:effectStyle><a:effectStyle><a:effectLst/></a:effectStyle></a:effectStyleLst><a:bgFillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="phClr"><a:tint val="95000"/><a:satMod val="170000"/></a:schemeClr></a:solidFill><a:gradFill rotWithShape="1"><a:gsLst><a:gs pos="0"><a:schemeClr val="phClr"><a:tint val="93000"/><a:satMod val="150000"/><a:shade val="98000"/><a:lumMod val="102000"/></a:schemeClr></a:gs><a:gs pos="50000"><a:schemeClr val="phClr"><a:tint val="98000"/><a:satMod val="130000"/><a:shade val="90000"/><a:lumMod val="103000"/></a:schemeClr></a:gs><a:gs pos="100000"><a:schemeClr val="phClr"><a:shade val="63000"/><a:satMod val="120000"/></a:schemeClr></a:gs></a:gsLst><a:lin ang="16200000" scaled="1"/></a:gradFill></a:bgFillStyleLst></a:fmtScheme></a:themeElements><a:objectDefaults/><a:extraClrSchemeLst/></a:theme>'''


def _shape(
    shape_id: int,
    name: str,
    x: int,
    y: int,
    width: int,
    height: int,
    *,
    fill: str | None = None,
    text: str = "",
    text_color: str = "F7FBFF",
    size: int = 2400,
    bold: bool = False,
    align: str = "l",
    radius: bool = False,
    font: str = "Aptos",
) -> str:
    fill_xml = (
        f'<a:solidFill><a:srgbClr val="{fill}"/></a:solidFill>' if fill else "<a:noFill/>"
    )
    geom = "roundRect" if radius else "rect"
    body = ""
    if text:
        body = f'''<p:txBody><a:bodyPr wrap="square" lIns="120000" rIns="120000" tIns="70000" bIns="70000" anchor="ctr"/><a:lstStyle/><a:p><a:pPr algn="{align}"/><a:r><a:rPr lang="en-US" sz="{size}" b="{1 if bold else 0}"><a:solidFill><a:srgbClr val="{text_color}"/></a:solidFill><a:latin typeface="{escape(font)}"/></a:rPr><a:t>{escape(text)}</a:t></a:r><a:endParaRPr lang="en-US" sz="{size}"/></a:p></p:txBody>'''
    return f'''<p:sp><p:nvSpPr><p:cNvPr id="{shape_id}" name="{escape(name)}"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:spPr><a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{width}" cy="{height}"/></a:xfrm><a:prstGeom prst="{geom}"><a:avLst/></a:prstGeom>{fill_xml}<a:ln><a:noFill/></a:ln></p:spPr>{body}</p:sp>'''


def _slide(shapes: str, name: str) -> str:
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld name="{escape(name)}"><p:bg><p:bgPr><a:solidFill><a:srgbClr val="{_COLOR['ink']}"/></a:solidFill><a:effectLst/></p:bgPr></p:bg><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>{shapes}</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>'''


def _pptx_members(spec: dict[str, Any], challenge: str) -> dict[str, str]:
    slides = spec["slides"]
    metric = max(spec["metrics"], key=lambda item: item["value"])
    slide1 = _slide(
        _shape(2, "Cyan rail", 560000, 540000, 110000, 4900000, fill=_COLOR["cyan"])
        + _shape(3, "Eyebrow", 930000, 650000, 5700000, 430000, text="EDSYS  /  WEEKLY ULTRA CONTROL", text_color=_COLOR["cyan"], size=1800, bold=True)
        + _shape(4, "Title", 900000, 1250000, 10100000, 1900000, text=slides[0]["title"], size=4700, bold=True, font="Aptos Display")
        + _shape(5, "Headline", 930000, 3250000, 9300000, 850000, text=slides[0]["body"], text_color=_COLOR["muted"], size=2500)
        + _shape(6, "Challenge", 930000, 5200000, 4200000, 500000, text=f"CHALLENGE  {challenge}", text_color=_COLOR["cyan"], size=1700, bold=True)
        + _shape(7, "Score", 8800000, 4600000, 2500000, 1200000, text="10 / 10", text_color=_COLOR["green"], size=5000, bold=True, align="r", font="Aptos Display"),
        "Maximum authority",
    )
    slide2 = _slide(
        _shape(2, "Title", 720000, 520000, 10500000, 850000, text=slides[1]["title"], size=3400, bold=True, font="Aptos Display")
        + _shape(3, "Body", 740000, 1400000, 9600000, 650000, text=slides[1]["body"], text_color=_COLOR["muted"], size=2100)
        + _shape(4, "Signal", 740000, 2600000, 3800000, 1750000, fill=_COLOR["surface"], text=f"{metric['value']}\n{metric['label']}", text_color=_COLOR["cyan"], size=4200, bold=True, radius=True, align="ctr", font="Aptos Display")
        + _shape(5, "Line", 5000000, 2870000, 5600000, 60000, fill=_COLOR["rule"])
        + _shape(6, "Proof one", 5000000, 2450000, 5600000, 650000, text="REAL MODEL  •  REAL TOOLS", size=2300, bold=True)
        + _shape(7, "Proof two", 5000000, 3200000, 5600000, 650000, text="RENDERED  •  REOPENED  •  VERIFIED", text_color=_COLOR["green"], size=2100, bold=True)
        + _shape(8, "Challenge", 740000, 5600000, 4700000, 400000, text=f"CONTROL ID  {challenge}", text_color=_COLOR["muted"], size=1500),
        "Observable proof",
    )
    slide3 = _slide(
        _shape(2, "Eyebrow", 720000, 600000, 4600000, 400000, text="RECOVERY IS PART OF POWER", text_color=_COLOR["cyan"], size=1700, bold=True)
        + _shape(3, "Title", 700000, 1150000, 10500000, 1000000, text=slides[2]["title"], size=3600, bold=True, font="Aptos Display")
        + _shape(4, "Body", 720000, 2150000, 10300000, 700000, text=slides[2]["body"], text_color=_COLOR["muted"], size=2200)
        + _shape(5, "Track", 1200000, 3800000, 9100000, 65000, fill=_COLOR["rule"])
        + _shape(6, "Plan node", 1050000, 3550000, 560000, 560000, fill=_COLOR["blue"], text="1", size=1900, bold=True, align="ctr", radius=True)
        + _shape(7, "Verify node", 5450000, 3550000, 560000, 560000, fill=_COLOR["cyan"], text="2", text_color=_COLOR["ink"], size=1900, bold=True, align="ctr", radius=True)
        + _shape(8, "Recover node", 9900000, 3550000, 560000, 560000, fill=_COLOR["green"], text="3", text_color=_COLOR["ink"], size=1900, bold=True, align="ctr", radius=True)
        + _shape(9, "Plan", 750000, 4300000, 1900000, 500000, text="PLAN", size=1900, bold=True, align="ctr")
        + _shape(10, "Verify", 4800000, 4300000, 1900000, 500000, text="VERIFY", size=1900, bold=True, align="ctr")
        + _shape(11, "Recover", 9250000, 4300000, 1900000, 500000, text="RECOVER", size=1900, bold=True, align="ctr")
        + _shape(12, "Challenge", 720000, 5750000, 4500000, 400000, text=f"VERIFIED  {challenge}", text_color=_COLOR["green"], size=1700, bold=True),
        "Recovery readiness",
    )
    content_types = ['<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>', '<Default Extension="xml" ContentType="application/xml"/>']
    content_types.extend(
        f'<Override PartName="/ppt/slides/slide{index}.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'
        for index in range(1, 4)
    )
    content_types.extend(
        [
            '<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>',
            '<Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>',
            '<Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>',
            '<Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>',
            '<Override PartName="/ppt/presProps.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presProps+xml"/>',
            '<Override PartName="/ppt/viewProps.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.viewProps+xml"/>',
            '<Override PartName="/ppt/tableStyles.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.tableStyles+xml"/>',
            '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>',
            '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>',
        ]
    )
    presentation_rels = [
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="slideMasters/slideMaster1.xml"/>'
    ]
    presentation_rels.extend(
        f'<Relationship Id="rId{index + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide{index}.xml"/>'
        for index in range(1, 4)
    )
    presentation_rels.extend(
        [
            '<Relationship Id="rId5" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/presProps" Target="presProps.xml"/>',
            '<Relationship Id="rId6" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/viewProps" Target="viewProps.xml"/>',
            '<Relationship Id="rId7" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/tableStyles" Target="tableStyles.xml"/>',
        ]
    )
    slide_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/></Relationships>'''
    members = {
        "[Content_Types].xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">' + "".join(content_types) + "</Types>",
        "_rels/.rels": '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/><Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/></Relationships>''',
        "docProps/core.xml": _core_properties(spec["title"]),
        "docProps/app.xml": '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"><Application>EdSys Fleet Autopilot</Application><PresentationFormat>On-screen Show (16:9)</PresentationFormat><Slides>3</Slides><Notes>0</Notes><HiddenSlides>0</HiddenSlides><MMClips>0</MMClips><ScaleCrop>false</ScaleCrop><Company>EdSys</Company><AppVersion>2.0</AppVersion></Properties>''',
        "ppt/presentation.xml": '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:presentation xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst><p:sldIdLst><p:sldId id="256" r:id="rId2"/><p:sldId id="257" r:id="rId3"/><p:sldId id="258" r:id="rId4"/></p:sldIdLst><p:sldSz cx="12192000" cy="6858000" type="screen16x9"/><p:notesSz cx="6858000" cy="9144000"/><p:defaultTextStyle><a:defPPr><a:defRPr lang="en-US"/></a:defPPr><a:lvl1pPr marL="0" algn="l" defTabSz="914400"><a:defRPr sz="1800"/></a:lvl1pPr></p:defaultTextStyle></p:presentation>''',
        "ppt/_rels/presentation.xml.rels": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' + "".join(presentation_rels) + "</Relationships>",
        "ppt/theme/theme1.xml": _office_theme(),
        "ppt/slideMasters/slideMaster1.xml": '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sldMaster xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr></p:spTree></p:cSld><p:clrMap accent1="accent1" accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" bg1="lt1" bg2="lt2" folHlink="folHlink" hlink="hlink" tx1="dk1" tx2="dk2"/><p:sldLayoutIdLst><p:sldLayoutId id="1" r:id="rId1"/></p:sldLayoutIdLst><p:txStyles><p:titleStyle><a:lvl1pPr algn="l"><a:defRPr sz="4400" b="1"/></a:lvl1pPr></p:titleStyle><p:bodyStyle><a:lvl1pPr marL="0" algn="l"><a:defRPr sz="2400"/></a:lvl1pPr></p:bodyStyle><p:otherStyle><a:defPPr><a:defRPr lang="en-US"/></a:defPPr></p:otherStyle></p:txStyles></p:sldMaster>''',
        "ppt/slideMasters/_rels/slideMaster1.xml.rels": '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme" Target="../theme/theme1.xml"/></Relationships>''',
        "ppt/slideLayouts/slideLayout1.xml": '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sldLayout xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" type="blank" preserve="1"><p:cSld name="Blank"><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr></p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>''',
        "ppt/slideLayouts/_rels/slideLayout1.xml.rels": '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="../slideMasters/slideMaster1.xml"/></Relationships>''',
        "ppt/presProps.xml": '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:presentationPr xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"/>''',
        "ppt/viewProps.xml": '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:viewPr xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" lastView="sldView"><p:normalViewPr/><p:slideViewPr><p:cSldViewPr><p:cViewPr varScale="1"><p:scale><a:sx n="100" d="100"/><a:sy n="100" d="100"/></p:scale><p:origin x="0" y="0"/></p:cViewPr><p:guideLst/></p:cSldViewPr></p:slideViewPr><p:notesTextViewPr><p:cViewPr><p:scale><a:sx n="100" d="100"/><a:sy n="100" d="100"/></p:scale><p:origin x="0" y="0"/></p:cViewPr></p:notesTextViewPr><p:gridSpacing cx="78028800" cy="78028800"/></p:viewPr>''',
        "ppt/tableStyles.xml": '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<a:tblStyleLst xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" def="{5C22544A-7EE6-4342-B048-85BDC9FD1C3A}"/>''',
        "ppt/slides/slide1.xml": slide1,
        "ppt/slides/slide2.xml": slide2,
        "ppt/slides/slide3.xml": slide3,
    }
    for index in range(1, 4):
        members[f"ppt/slides/_rels/slide{index}.xml.rels"] = slide_rels
    return members


def _validate_spec(path: Path, challenge: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArtifactCanaryError(f"invalid canary spec: {exc}") from exc
    if not isinstance(value, dict) or value.get("challenge") != challenge:
        raise ArtifactCanaryError("canary spec challenge mismatch")
    for key, limit in (("title", 80), ("headline", 140)):
        text = value.get(key)
        if not isinstance(text, str) or not text.strip() or len(text) > limit:
            raise ArtifactCanaryError(f"canary spec {key} is invalid")
        value[key] = text.strip()
    metrics = value.get("metrics")
    if not isinstance(metrics, list) or not 3 <= len(metrics) <= 5:
        raise ArtifactCanaryError("canary spec requires three to five metrics")
    for metric in metrics:
        if not isinstance(metric, dict):
            raise ArtifactCanaryError("canary metric is invalid")
        if not isinstance(metric.get("label"), str) or not metric["label"].strip():
            raise ArtifactCanaryError("canary metric label is invalid")
        if not isinstance(metric.get("value"), int) or not 0 <= metric["value"] <= 100:
            raise ArtifactCanaryError("canary metric value is invalid")
        if metric.get("status") not in {"passed", "verified", "ready"}:
            raise ArtifactCanaryError("canary metric status is invalid")
        metric["label"] = metric["label"].strip()[:48]
        metric["status"] = str(metric["status"]).upper()
    slides = value.get("slides")
    if not isinstance(slides, list) or len(slides) != 3:
        raise ArtifactCanaryError("canary spec requires exactly three slides")
    for slide in slides:
        if not isinstance(slide, dict):
            raise ArtifactCanaryError("canary slide is invalid")
        for key, limit in (("title", 72), ("body", 150)):
            text = slide.get(key)
            if not isinstance(text, str) or not text.strip() or len(text) > limit:
                raise ArtifactCanaryError(f"canary slide {key} is invalid")
            slide[key] = text.strip()
    return value


def _validate_package(path: Path, required: set[str], challenge: str) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            if not required.issubset(names):
                raise ArtifactCanaryError(f"{path.name} is missing required OpenXML members")
            if archive.testzip() is not None:
                raise ArtifactCanaryError(f"{path.name} has a corrupt ZIP member")
            text = ""
            for name in names:
                if name.endswith((".xml", ".rels")):
                    payload = archive.read(name)
                    ElementTree.fromstring(payload)
                    text += payload.decode("utf-8", errors="replace")
            if challenge not in text:
                raise ArtifactCanaryError(f"{path.name} does not contain the challenge")
    except zipfile.BadZipFile as exc:
        raise ArtifactCanaryError(f"{path.name} is not valid OpenXML") from exc


def _run(argv: list[str], timeout: int = 120) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[-800:]
        raise ArtifactCanaryError(f"command failed ({argv[0]}): {detail}")
    return result


def _render(source: Path, render_dir: Path, profile_dir: Path) -> tuple[Path, list[Path]]:
    profile_uri = profile_dir.resolve().as_uri()
    _run(
        [
            "soffice",
            "--headless",
            "--nologo",
            "--nodefault",
            "--nolockcheck",
            "--norestore",
            f"-env:UserInstallation={profile_uri}",
            "--convert-to",
            "pdf",
            "--outdir",
            str(render_dir),
            str(source),
        ],
        timeout=180,
    )
    pdf = render_dir / f"{source.stem}.pdf"
    if not pdf.is_file() or pdf.stat().st_size < 1000:
        raise ArtifactCanaryError(f"{source.name} did not render to a usable PDF")
    prefix = render_dir / f"{source.stem}-page"
    _run(["pdftoppm", "-png", "-r", "110", str(pdf), str(prefix)], timeout=120)
    previews = sorted(render_dir.glob(f"{source.stem}-page-*.png"))
    if not previews:
        raise ArtifactCanaryError(f"{source.name} did not render preview pages")
    for preview in previews:
        payload = preview.read_bytes()
        if len(payload) < 24 or payload[:8] != b"\x89PNG\r\n\x1a\n":
            raise ArtifactCanaryError(f"{preview.name} is not a valid PNG preview")
        width = int.from_bytes(payload[16:20], "big")
        height = int.from_bytes(payload[20:24], "big")
        if width < 600 or height < 400:
            raise ArtifactCanaryError(f"{preview.name} preview is unexpectedly small")
    return pdf, previews


def _rendered_text(pdf: Path) -> str:
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as handle:
        target = Path(handle.name)
    try:
        _run(["pdftotext", str(pdf), str(target)])
        return target.read_text(encoding="utf-8", errors="replace")
    finally:
        target.unlink(missing_ok=True)


def run_artifact_canary(
    workspace: Path,
    retained_dir: Path,
    challenge: str,
    spec_path: Path,
) -> dict[str, Any]:
    if not _HEX.fullmatch(challenge):
        raise ArtifactCanaryError("challenge must be sixteen lowercase hexadecimal characters")
    workspace = workspace.expanduser().resolve()
    retained_dir = retained_dir.expanduser().resolve()
    spec_path = spec_path.expanduser().resolve()
    allowed_workspace_root = (Path.home() / ".local" / "state" / "edsys-fleet-benchmark").resolve()
    allowed_retained_root = Path("/mnt/ai-store/private/fleet-autopilot/benchmarks").resolve()
    if workspace.parent != allowed_workspace_root or not workspace.name.startswith("ultra-"):
        raise ArtifactCanaryError("workspace is outside the dedicated Fleet Ultra root")
    if allowed_retained_root not in retained_dir.parents or retained_dir.name != "ultra-artifacts":
        raise ArtifactCanaryError("retained directory is outside the private benchmark root")
    if spec_path.parent != workspace or spec_path.name != "artifact-canary-spec.json":
        raise ArtifactCanaryError("spec must use the dedicated workspace filename")
    workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
    retained_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(workspace, 0o700)
    os.chmod(retained_dir, 0o700)
    spec = _validate_spec(spec_path, challenge)
    build_dir = workspace / "artifacts"
    render_dir = workspace / "rendered"
    profile_root = workspace / "office-profiles"
    build_dir.mkdir(mode=0o700)
    render_dir.mkdir(mode=0o700)
    profile_root.mkdir(mode=0o700)
    workbook = build_dir / "edsys-ultra-workbook.xlsx"
    presentation = build_dir / "edsys-ultra-presentation.pptx"
    _write_zip(workbook, _xlsx_members(spec, challenge))
    _write_zip(presentation, _pptx_members(spec, challenge))
    _validate_package(
        workbook,
        {
            "xl/workbook.xml",
            "xl/styles.xml",
            "xl/worksheets/sheet1.xml",
            "xl/worksheets/sheet2.xml",
        },
        challenge,
    )
    _validate_package(
        presentation,
        {
            "ppt/presentation.xml",
            "ppt/slides/slide1.xml",
            "ppt/slides/slide2.xml",
            "ppt/slides/slide3.xml",
        },
        challenge,
    )
    workbook_pdf, workbook_previews = _render(
        workbook, render_dir, profile_root / "workbook"
    )
    presentation_pdf, presentation_previews = _render(
        presentation, render_dir, profile_root / "presentation"
    )
    workbook_text = _rendered_text(workbook_pdf)
    presentation_text = _rendered_text(presentation_pdf)
    if challenge not in workbook_text:
        raise ArtifactCanaryError("rendered workbook does not contain the challenge")
    if challenge not in presentation_text:
        raise ArtifactCanaryError("rendered presentation does not contain the challenge")
    if len(presentation_previews) != 3:
        raise ArtifactCanaryError("rendered presentation does not contain exactly three slides")
    if not 1 <= len(workbook_previews) <= 4:
        raise ArtifactCanaryError("rendered workbook page count is outside the canary contract")
    retained_sources = [workbook, presentation, workbook_pdf, presentation_pdf]
    retained_sources.extend(workbook_previews)
    retained_sources.extend(presentation_previews)
    files: dict[str, dict[str, Any]] = {}
    for source in retained_sources:
        destination = retained_dir / source.name
        shutil.copy2(source, destination)
        destination.chmod(0o600)
        files[destination.name] = {
            "bytes": destination.stat().st_size,
            "sha256": _sha256(destination),
        }
    report = {
        "schema_version": 1,
        "status": "passed",
        "challenge_sha256": hashlib.sha256(challenge.encode()).hexdigest(),
        "spec_sha256": _sha256(spec_path),
        "workbook": {
            "editable_openxml": True,
            "formula_verified": True,
            "sheets": 2,
            "rendered_pages": len(workbook_previews),
            "rendered_challenge_verified": True,
        },
        "presentation": {
            "editable_openxml": True,
            "slides": 3,
            "rendered_pages": len(presentation_previews),
            "rendered_challenge_verified": True,
        },
        "files": files,
    }
    report_path = retained_dir / "canary-report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report_path.chmod(0o600)
    return report


def validate_retained_canary(retained_dir: Path, challenge: str) -> dict[str, Any]:
    report_path = retained_dir / "canary-report.json"
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArtifactCanaryError(f"retained canary report is invalid: {exc}") from exc
    if report.get("schema_version") != 1 or report.get("status") != "passed":
        raise ArtifactCanaryError("retained canary report did not pass")
    if report.get("challenge_sha256") != hashlib.sha256(challenge.encode()).hexdigest():
        raise ArtifactCanaryError("retained canary challenge mismatch")
    workbook = report.get("workbook", {})
    presentation = report.get("presentation", {})
    if not (
        workbook.get("editable_openxml")
        and workbook.get("formula_verified")
        and workbook.get("sheets") == 2
        and workbook.get("rendered_challenge_verified")
        and presentation.get("editable_openxml")
        and presentation.get("slides") == 3
        and presentation.get("rendered_pages") == 3
        and presentation.get("rendered_challenge_verified")
    ):
        raise ArtifactCanaryError("retained canary contract is incomplete")
    files = report.get("files")
    if not isinstance(files, dict) or not files:
        raise ArtifactCanaryError("retained canary file inventory is missing")
    for name, metadata in files.items():
        if not isinstance(name, str) or Path(name).name != name or not isinstance(metadata, dict):
            raise ArtifactCanaryError("retained canary file metadata is invalid")
        path = retained_dir / name
        if not path.is_file() or _sha256(path) != metadata.get("sha256"):
            raise ArtifactCanaryError(f"retained canary file hash mismatch: {name}")
    _validate_package(
        retained_dir / "edsys-ultra-workbook.xlsx",
        {"xl/workbook.xml", "xl/styles.xml", "xl/worksheets/sheet1.xml"},
        challenge,
    )
    _validate_package(
        retained_dir / "edsys-ultra-presentation.pptx",
        {"ppt/presentation.xml", "ppt/slides/slide1.xml", "ppt/slides/slide3.xml"},
        challenge,
    )
    return {
        "status": "passed",
        "workbook_sheets": workbook["sheets"],
        "workbook_pages": workbook["rendered_pages"],
        "presentation_slides": presentation["slides"],
        "presentation_pages": presentation["rendered_pages"],
        "file_count": len(files),
    }
