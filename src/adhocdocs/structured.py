"""Structured text of a schema no one has told us: JSON, JSON Lines, XML.

The syntax of these formats is standard, so two things can be done without
knowing what the data means:

- **Pointers** (``ptr=``, RFC 6901 JSON Pointer) name a value by its path,
  and resolve to the char range of that value's text in the layer. The
  layer is the file's literal text, so a value quoted by pointer is quoted
  verbatim: ``"amount": 1250.00`` stays ``1250.00``, and ``"Caf\\u00e9"``
  stays as written, escapes and all. A string's range is what is between
  its quote marks, which are JSON's syntax rather than the value. In JSON
  Lines the first token is the record's number, counting from 0
  (``/12/name``). A key that appears twice in one object makes a pointer
  through it an error rather than a guess.
- **Schemas** (:func:`schema`): every path in the data with the types and
  counts found there, and a few examples, each with its pointer. That is
  the starting point for working out what the data is, which is
  interpretation (see adhocdocs.interp), not quotation.

Offsets are code points into the layer text, as everywhere else.
"""

import json
import re
from collections import Counter

_WS = re.compile(r"[ \t\n\r]*")
_DECODER = json.JSONDecoder()
EXAMPLES = 3
EXAMPLE_CHARS = 80


class PointerError(ValueError):
    pass


def _ws(text: str, i: int) -> int:
    return _WS.match(text, i).end()


def _value_end(text: str, i: int) -> int:
    try:
        return _DECODER.raw_decode(text, i)[1]
    except json.JSONDecodeError as e:
        raise PointerError(f"not valid JSON at char {e.pos}: {e.msg}")


def parse_pointer(ptr: str) -> list[str]:
    """RFC 6901: ``""`` is the whole document, else ``/``-separated tokens
    with ``~1`` for ``/`` and ``~0`` for ``~``."""
    if ptr == "":
        return []
    if not ptr.startswith("/"):
        raise PointerError(f"a JSON pointer starts with /: {ptr!r}")
    return [t.replace("~1", "/").replace("~0", "~") for t in ptr[1:].split("/")]


def format_pointer(tokens) -> str:
    return "".join("/" + str(t).replace("~", "~0").replace("/", "~1") for t in tokens)


def records(text: str) -> list[tuple[int, int]]:
    """The char ranges of a JSON Lines text's records (non-blank lines)."""
    out, pos = [], 0
    for line in text.split("\n"):
        s = pos + len(line) - len(line.lstrip(" \t\r"))
        e = pos + len(line.rstrip(" \t\r"))
        if e > s:
            out.append((s, e))
        pos += len(line) + 1
    return out


def _walk(text: str, i: int, tokens: list[str], done: list[str]) -> tuple[int, int]:
    """The range of the value at ``tokens`` below the value starting at ``i``."""
    i = _ws(text, i)
    if not tokens:
        e = _value_end(text, i)
        return (i + 1, e - 1) if text[i] == '"' else (i, e)
    tok, rest = tokens[0], tokens[1:]
    here = format_pointer(done) or "the top"
    c = text[i:i + 1]
    if c == "{":
        j = _ws(text, i + 1)
        found = None
        if text[j:j + 1] == "}":
            raise PointerError(f"no key {tok!r} in {here}")
        while True:
            if text[j:j + 1] != '"':
                raise PointerError(f"not valid JSON at char {j}: expected a key")
            key, j = json.decoder.scanstring(text, j + 1)
            j = _ws(text, j)
            if text[j:j + 1] != ":":
                raise PointerError(f"not valid JSON at char {j}: expected ':'")
            v = _ws(text, j + 1)
            end = _value_end(text, v)
            if key == tok:
                if found is not None:
                    raise PointerError(f"key {tok!r} appears more than once in {here}; a pointer can't say which")
                found = v
            j = _ws(text, end)
            if text[j:j + 1] == ",":
                j = _ws(text, j + 1)
                continue
            if text[j:j + 1] == "}":
                break
            raise PointerError(f"not valid JSON at char {j}: expected ',' or '}}'")
        if found is None:
            raise PointerError(f"no key {tok!r} in {here}")
        return _walk(text, found, rest, done + [tok])
    if c == "[":
        if not re.fullmatch(r"0|[1-9][0-9]*", tok):
            raise PointerError(f"{here} is an array: {tok!r} isn't an index")
        want = int(tok)
        j = _ws(text, i + 1)
        n = 0
        if text[j:j + 1] == "]":
            raise PointerError(f"{here} is an empty array")
        while True:
            end = _value_end(text, j)
            if n == want:
                return _walk(text, j, rest, done + [tok])
            n += 1
            j = _ws(text, end)
            if text[j:j + 1] == ",":
                j = _ws(text, j + 1)
                continue
            if text[j:j + 1] == "]":
                break
            raise PointerError(f"not valid JSON at char {j}: expected ',' or ']'")
        raise PointerError(f"{here} has {n} items: no index {want}")
    raise PointerError(f"{here} is a {type_of(_DECODER.raw_decode(text, i)[0])}, not an object or array: no {tok!r} in it")


def pointer_range(text: str, ptr: str, lines: bool = False) -> tuple[int, int]:
    """The char range of the value a JSON pointer names in ``text``: a JSON
    document, or with ``lines`` a JSON Lines text, whose first token picks
    the record."""
    tokens = parse_pointer(ptr)
    if lines:
        recs = records(text)
        if not tokens:
            raise PointerError("in JSON Lines a pointer starts with the record's number, e.g. /0")
        if not re.fullmatch(r"0|[1-9][0-9]*", tokens[0]) or int(tokens[0]) >= len(recs):
            raise PointerError(f"no record {tokens[0]!r} (there are {len(recs)}, numbered from 0)")
        s, e = recs[int(tokens[0])]
        a, b = _walk(text, s, tokens[1:], [tokens[0]])
        if b > e:
            raise PointerError(f"record {tokens[0]} isn't one JSON value on its line")
        return a, b
    s = _ws(text, 0)
    a, b = _walk(text, s, tokens, [])
    if not tokens and _ws(text, b) != len(text):
        raise PointerError("there is more than one JSON value here; is it JSON Lines?")
    return a, b


# -- recognizing -------------------------------------------------------------------------------

def sniff(text: str) -> str | None:
    """``json``, ``ndjson`` or ``xml`` if the text is one, else None."""
    head = text.lstrip("﻿ \t\r\n")
    if head.startswith(("{", "[")):
        try:
            json.loads(text)
            return "json"
        except ValueError:
            pass
        recs = records(text)
        if len(recs) >= 2 and all(text[s] in "{[" for s, _ in recs[:50]):
            try:
                for s, e in recs:
                    json.loads(text[s:e])
                return "ndjson"
            except ValueError:
                return None
        return None
    if head.startswith("<?xml"):
        return "xml"
    return None


def loads(text: str, kind: str):
    """The decoded data: one value, or for JSON Lines the list of records."""
    if kind == "ndjson":
        return [json.loads(text[s:e]) for s, e in records(text)]
    return json.loads(text)


# -- schemas -----------------------------------------------------------------------------------

def type_of(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, (int, float)):
        return "number"
    if isinstance(v, str):
        return "string"
    if isinstance(v, list):
        return "array"
    return "object"


def _short(v) -> str:
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    return s if len(s) <= EXAMPLE_CHARS else s[:EXAMPLE_CHARS - 1] + "…"


def schema(data, records_: bool = False, limit_paths: int = 500) -> dict:
    """What the data holds, path by path: ``/items/*/amount`` (``*`` for any
    array index) with the types found there and how often, how many of its
    parent objects have it, and examples with the pointer of each. With
    ``records_``, ``data`` is JSON Lines' records and the first step is the
    record number."""
    paths: dict[str, dict] = {}
    parents: Counter = Counter()

    def visit(v, generic: list, real: list):
        key = format_pointer(generic) if generic else ""
        p = paths.get(key)
        if p is None:
            if len(paths) >= limit_paths:
                return
            p = paths[key] = {"path": key or "/", "types": Counter(), "count": 0, "examples": []}
        t = type_of(v)
        p["types"][t] += 1
        p["count"] += 1
        if t not in ("object", "array") and len(p["examples"]) < EXAMPLES:
            p["examples"].append({"value": _short(v), "ptr": format_pointer(real)})
        if t == "object":
            parents[key] += 1
            for k, x in v.items():
                visit(x, generic + [k], real + [k])
        elif t == "array":
            p.setdefault("lengths", []).append(len(v))
            for i, x in enumerate(v):
                visit(x, generic + ["*"], real + [i])

    if records_:
        for i, rec in enumerate(data):
            visit(rec, ["*"], [i])
    else:
        visit(data, [], [])
    out = []
    for key, p in paths.items():
        row = {"path": p["path"], "types": dict(p["types"]), "count": p["count"]}
        parent = key.rsplit("/", 1)[0] if key else None
        if parent is not None and parents.get(parent) and not key.endswith("/*"):
            row["in_objects"] = f"{p['count']} of {parents[parent]}"
        if "lengths" in p:
            ls = p["lengths"]
            row["length"] = [min(ls), max(ls)] if min(ls) != max(ls) else ls[0]
        if p["examples"]:
            row["examples"] = p["examples"]
        out.append(row)
    shape = type_of(data) if not records_ else f"{len(data)} records"
    return {"shape": shape, "paths": out, **({"truncated": True} if len(paths) >= limit_paths else {})}


def xml_schema(data: bytes, limit_paths: int = 500) -> dict:
    """Element paths in an XML file (``/root/item/@id`` for attributes),
    with counts and examples of their text. XML has no pointers here yet."""
    from lxml import etree
    paths: dict[str, dict] = {}
    stack: list[str] = []
    for event, el in etree.iterparse(__import__("io").BytesIO(data), events=("start", "end"),
                                     resolve_entities=False, no_network=True, huge_tree=True):
        if not isinstance(el.tag, str):
            continue
        tag = etree.QName(el).localname
        if event == "start":
            stack.append(tag)
            key = "/" + "/".join(stack)
            items = [(key, None)] + [(key + "/@" + etree.QName(a).localname, v) for a, v in el.attrib.items()]
            for k, v in items:
                p = paths.get(k)
                if p is None:
                    if len(paths) >= limit_paths:
                        continue
                    p = paths[k] = {"path": k, "count": 0, "examples": []}
                p["count"] += 1
                if v is not None and len(p["examples"]) < EXAMPLES:
                    p["examples"].append(_short(v))
        else:
            key = "/" + "/".join(stack)
            text = (el.text or "").strip()
            p = paths.get(key)
            if p is not None and text and len(el) == 0 and len(p["examples"]) < EXAMPLES:
                p["examples"].append(_short(text))
            stack.pop()
            el.clear()
    return {"shape": "xml", "paths": [{k: v for k, v in p.items() if v != []} for p in paths.values()],
            **({"truncated": True} if len(paths) >= limit_paths else {})}
