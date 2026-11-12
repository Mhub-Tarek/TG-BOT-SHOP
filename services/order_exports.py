from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
import re
from xml.sax.saxutils import escape as xml_escape
from zipfile import ZIP_DEFLATED, ZipFile


XLSX_MIME_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
TXT_MIME_TYPE = "text/plain; charset=utf-8"


@dataclass(frozen=True)
class OrderExport:
    filename: str
    mime_type: str
    data: bytes


def normalize_export_format(fmt: str | None) -> str | None:
    fmt = (fmt or "").strip().lower()
    if fmt in {"txt", "text"}:
        return "txt"
    if fmt in {"xlsx", "excel"}:
        return "xlsx"
    return None


def build_order_export(
    *,
    order,
    product,
    contents: Iterable[str],
    fmt: str,
) -> OrderExport:
    """Build a buyer download for the exact stock contents tied to an order."""
    export_fmt = normalize_export_format(fmt)
    if export_fmt is None:
        raise ValueError(f"Unsupported export format: {fmt!r}")

    items = [str(c) for c in contents]
    product_name = str(_row_value(product, "name", "Product"))
    order_id = int(_row_value(order, "id", 0) or 0)
    stem = _safe_filename_part(product_name, "order")
    filename = f"{stem}_order_{order_id}.{export_fmt}"

    if export_fmt == "txt":
        return OrderExport(
            filename=filename,
            mime_type=TXT_MIME_TYPE,
            data=_build_txt(product_name, order_id, items),
        )

    return OrderExport(
        filename=filename,
        mime_type=XLSX_MIME_TYPE,
        data=_build_xlsx(product_name, order_id, items),
    )


def _row_value(row, key: str, default=None):
    if row is None:
        return default
    if isinstance(row, Mapping):
        return row.get(key, default)
    try:
        keys = row.keys()
    except AttributeError:
        return default
    return row[key] if key in keys else default


def _safe_filename_part(value: str, fallback: str) -> str:
    name = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_")
    name = name[:60].strip("_")
    return name or fallback


def _build_txt(product_name: str, order_id: int, items: list[str]) -> bytes:
    lines = [
        f"Product: {product_name}",
        f"Order: #{order_id}",
        f"Items: {len(items)}",
        "",
    ]
    lines.extend(f"{idx}. {content}" for idx, content in enumerate(items, start=1))
    return ("\n".join(lines).rstrip() + "\n").encode("utf-8")


def _build_xlsx(product_name: str, order_id: int, items: list[str]) -> bytes:
    rows: list[list[str]] = [
        ["Product", product_name],
        ["Order", f"#{order_id}"],
        ["Items", str(len(items))],
        [],
        ["#", "Value"],
    ]
    rows.extend([str(idx), content] for idx, content in enumerate(items, start=1))

    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    out = BytesIO()
    with ZipFile(out, "w", ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _content_types_xml())
        zf.writestr("_rels/.rels", _root_rels_xml())
        zf.writestr("docProps/app.xml", _app_props_xml())
        zf.writestr("docProps/core.xml", _core_props_xml(now))
        zf.writestr("xl/workbook.xml", _workbook_xml())
        zf.writestr("xl/_rels/workbook.xml.rels", _workbook_rels_xml())
        zf.writestr("xl/styles.xml", _styles_xml())
        zf.writestr("xl/worksheets/sheet1.xml", _worksheet_xml(rows))
    return out.getvalue()


def _content_types_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>"""


def _root_rels_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>"""


def _app_props_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
            xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
  <Application>Telegram Shop</Application>
</Properties>"""


def _core_props_xml(created_at: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
                   xmlns:dc="http://purl.org/dc/elements/1.1/"
                   xmlns:dcterms="http://purl.org/dc/terms/"
                   xmlns:dcmitype="http://purl.org/dc/dcmitype/"
                   xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <dc:creator>Telegram Shop</dc:creator>
  <cp:lastModifiedBy>Telegram Shop</cp:lastModifiedBy>
  <dcterms:created xsi:type="dcterms:W3CDTF">{created_at}</dcterms:created>
  <dcterms:modified xsi:type="dcterms:W3CDTF">{created_at}</dcterms:modified>
</cp:coreProperties>"""


def _workbook_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
          xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>
    <sheet name="Order" sheetId="1" r:id="rId1"/>
  </sheets>
</workbook>"""


def _workbook_rels_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>"""


def _styles_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>
  <fills count="1"><fill><patternFill patternType="none"/></fill></fills>
  <borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
  <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
  <cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs>
</styleSheet>"""


def _worksheet_xml(rows: list[list[str]]) -> str:
    body: list[str] = []
    for row_idx, row in enumerate(rows, start=1):
        cells = []
        for col_idx, value in enumerate(row, start=1):
            ref = f"{_col_name(col_idx)}{row_idx}"
            text = str(value)
            space = ' xml:space="preserve"' if text.strip() != text or "\n" in text else ""
            cells.append(
                f'<c r="{ref}" t="inlineStr"><is><t{space}>'
                f"{xml_escape(text)}</t></is></c>"
            )
        body.append(f'<row r="{row_idx}">{"".join(cells)}</row>')

    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <cols><col min="1" max="1" width="10" customWidth="1"/><col min="2" max="2" width="80" customWidth="1"/></cols>
  <sheetData>
    {''.join(body)}
  </sheetData>
</worksheet>"""


def _col_name(index: int) -> str:
    name = ""
    while index:
        index, rem = divmod(index - 1, 26)
        name = chr(65 + rem) + name
    return name
