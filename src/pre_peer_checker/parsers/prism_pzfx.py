"""GraphPad Prism ``.pzfx`` (XML) lightweight reader.

Extracts table titles and Y-column numeric vectors for Entity Linking.
Binary ``.pzf`` is detected but not parsed (ask users to Save As .pzfx).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from pathlib import Path


def _local(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


@dataclass
class PrismColumn:
    title: str
    kind: str  # X / Y / RowTitle
    values: tuple[float, ...]
    n: int


@dataclass
class PrismTable:
    title: str
    table_id: str
    columns: list[PrismColumn] = field(default_factory=list)

    def y_vectors(self) -> list[tuple[str, tuple[float, ...]]]:
        out: list[tuple[str, tuple[float, ...]]] = []
        for col in self.columns:
            if col.kind == "Y" and col.n > 0:
                key = col.title or "Y"
                out.append((key, col.values))
        return out


@dataclass
class PrismFile:
    path: Path
    tables: list[PrismTable] = field(default_factory=list)
    backend: str = "pzfx-xml"

    def to_dict(self) -> dict:
        return {
            "path": str(self.path),
            "backend": self.backend,
            "source_kind": "prism",
            "tables": [
                {
                    "title": t.title,
                    "table_id": t.table_id,
                    "columns": [asdict(c) for c in t.columns],
                    "y_groups": [
                        {"group": g, "n": len(v), "values": list(v)}
                        for g, v in t.y_vectors()
                    ],
                }
                for t in self.tables
            ],
            # Uniform DAG-ish shape for n_matrix / script_resolve
            "reads": [
                {
                    "function": "prism_table",
                    "path": str(self.path),
                    "assigned_to": t.title or t.table_id,
                    "resolved_path": str(self.path),
                }
                for t in self.tables
            ],
            "plots": [],
            "saves": [],
        }


def _parse_subcolumn_values(sub: ET.Element) -> list[float]:
    vals: list[float] = []
    for child in sub:
        if _local(child.tag) != "d":
            continue
        # Excluded cells often have Excluded="1"
        if (child.attrib.get("Excluded") or "").strip() in {"1", "true", "True"}:
            continue
        text = (child.text or "").strip()
        if not text or text == "*":
            continue
        try:
            vals.append(float(text))
        except ValueError:
            continue
    return vals


def _parse_column(elem: ET.Element, kind: str) -> PrismColumn | None:
    title = ""
    values: list[float] = []
    for child in elem:
        tag = _local(child.tag)
        if tag == "Title":
            title = (child.text or "").strip()
        elif tag == "Subcolumn":
            values.extend(_parse_subcolumn_values(child))
    if kind == "RowTitle":
        return PrismColumn(title=title or "ROW", kind=kind, values=tuple(), n=0)
    return PrismColumn(title=title, kind=kind, values=tuple(values), n=len(values))


def parse_pzfx(path: Path | str) -> PrismFile:
    path = Path(path)
    tree = ET.parse(path)
    root = tree.getroot()
    tables: list[PrismTable] = []
    for elem in root.iter():
        tag = _local(elem.tag)
        if tag not in {"Table", "HugeTable"}:
            continue
        title = ""
        table_id = elem.attrib.get("ID") or ""
        cols: list[PrismColumn] = []
        for child in elem:
            ct = _local(child.tag)
            if ct == "Title":
                title = (child.text or "").strip()
            elif ct == "XColumn":
                col = _parse_column(child, "X")
                if col:
                    cols.append(col)
            elif ct == "YColumn":
                col = _parse_column(child, "Y")
                if col:
                    cols.append(col)
            elif ct == "RowTitlesColumn":
                col = _parse_column(child, "RowTitle")
                if col:
                    cols.append(col)
        tables.append(PrismTable(title=title or table_id or f"Table{len(tables)+1}", table_id=table_id, columns=cols))
    return PrismFile(path=path, tables=tables)


def is_prism_binary(path: Path | str) -> bool:
    """True for legacy binary .pzf (not XML)."""
    path = Path(path)
    if path.suffix.lower() != ".pzf":
        return False
    head = path.read_bytes()[:64]
    return b"<?xml" not in head.lower() and b"<graphpad" not in head.lower()
