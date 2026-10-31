"""List files (``*.list.jsonl``): a header line, then one item per line.

Header: ``{"type": "list", "v": 1, "title", "columns", "made_by", "query"}``.
Item: ``{"loc", "group"?, "fields"?, "children"?}``, where ``loc`` is a
locator in string or JSON form. Order is the file's order.
"""

import json
from datetime import date

from . import locators

DEFAULT_COLUMNS = ["date", "name", "kind", "custodian", "tags"]


def read(path) -> tuple[dict, list[dict]]:
    with open(path) as f:
        rows = [json.loads(line) for line in f if line.strip()]
    if not rows or rows[0].get("type") != "list":
        raise ValueError(f"{path} is not a list file")
    return rows[0], rows[1:]


def write(path, header: dict, items: list[dict]):
    with open(path, "w") as f:
        for row in [header, *items]:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def new(title: str, locs, columns=None, made_by="claude", query=None) -> tuple[dict, list[dict]]:
    header = {"type": "list", "v": 1, "title": title, "columns": columns or DEFAULT_COLUMNS, "made_by": made_by}
    if query:
        header["query"] = query
    return header, [{"loc": l} for l in locs]


def doc_of(item: dict) -> str:
    return locators.parse(item["loc"])["doc"]


def sort(items, fields: dict, by: str, desc=False):
    def key(it):
        v = (it.get("fields") or {}).get(by)
        if v is None:
            v = (fields.get(doc_of(it)) or {}).get(by)
        return (v is None, v if not isinstance(v, list) else ",".join(v))
    return sorted(items, key=key, reverse=desc)


def group(items, fields: dict, by: str):
    out = []
    for it in items:
        row = fields.get(doc_of(it)) or {}
        if by == "week":
            d = row.get("date")
            g = "undated"
            if d:
                y, w, _ = date.fromisoformat(d[:10]).isocalendar()
                g = f"{y}-W{w:02d}"
        else:
            v = row.get(by)
            g = ", ".join(v) if isinstance(v, list) else (v or f"no {by}")
        out.append({**it, "group": g})
    # groups appear in order of first occurrence; keep items together
    order = list(dict.fromkeys(it["group"] for it in out))
    return sorted(out, key=lambda it: order.index(it["group"]))
