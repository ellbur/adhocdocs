"""Command-line entry points. Every command prints JSON (or JSON Lines).

The matter is found from ``--matter``, ``$ADHOC_MATTER``, or the nearest
directory above the current one that has a ``store/``.
"""

import argparse
import json
import shutil
import sys

from . import lists, locators
from .matter import Matter, MatterNotFound


def out(obj):
    print(json.dumps(obj, ensure_ascii=False))


def parser(prog, doc):
    p = argparse.ArgumentParser(prog=prog, description=doc, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--matter", help="matter directory (default: $ADHOC_MATTER or the enclosing matter)")
    return p


def run(fn):
    """Errors as one JSON line on stderr and exit status 1, not tracebacks."""
    def wrapper(argv=None):
        try:
            return fn(argv)
        except (MatterNotFound, locators.LocatorError, ValueError, KeyError) as e:
            print(json.dumps({"error": str(e).strip("'\"")}), file=sys.stderr)
            sys.exit(1)
        except BrokenPipeError:
            sys.exit(0)
    return wrapper


# -- adhoc: host-side matter administration -------------------------------------------

@run
def adhoc(argv=None):
    p = parser("adhoc", "Matter administration (host side).")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("init", help="create an empty matter")
    s.add_argument("path")
    s = sub.add_parser("ingest", help="add files or directories to the matter")
    s.add_argument("paths", nargs="+")
    s.add_argument("--custodian", help="custodian ID, e.g. smith-j (part of document identity)")
    s.add_argument("--collection", required=True, help="collection name, e.g. smith-laptop-2026-03")
    s = sub.add_parser("withdraw", help="remove a document from the matter (e.g. clawback)")
    s.add_argument("doc")
    s.add_argument("--reason", required=True)
    a = p.parse_args(argv)

    if a.cmd == "init":
        m = Matter.create(a.path)
        out({"matter": str(m.root)})
        return
    m = Matter.find(a.matter)
    if a.cmd == "ingest":
        from .ingest import ingest_paths

        def report(doc, name, ext):
            out({"doc": doc, "name": name, "kind": ext.kind,
                 "layers": [{"view": l.view, "chars": len(l.text)} for l in ext.layers],
                 "children": len(ext.children)})
        ingest_paths(m, a.paths, a.custodian, a.collection, report)
    elif a.cmd == "withdraw":
        doc = locators.parse("doc:" + a.doc.removeprefix("doc:"), m.objects)["doc"]
        out(m.logs["store"].append(op="withdraw", doc=doc, reason=a.reason))


# -- layers and text ---------------------------------------------------------------------

@run
def layers(argv=None):
    p = parser("layers", "List the text layers of a document (or content hash).")
    p.add_argument("doc")
    a = p.parse_args(argv)
    m = Matter.find(a.matter)
    h = locators.parse("doc:" + a.doc.removeprefix("doc:"), m.objects)["doc"]
    state = m.state
    content = state.documents.get(h, h)
    primary = state.primary_layer(content)
    for l in state.layers.get(content, []):
        obj = m.objects.get_object(l.id)
        out({"layer": l.id, "view": l.view, "primary": primary is not None and l.id == primary.id,
             "chars": len(m.objects.get_text(l.text)), "text_path": str(m.objects.path(l.text)),
             "parts": sorted(k for k in ("segments", "geometry", "presentation") if k in obj),
             "feed": l.feed})


@run
def text(argv=None):
    p = parser("text", "Print the text at a locator, after checking it.")
    p.add_argument("locator", help="string or JSON locator")
    p.add_argument("--path", action="store_true", help="print the layer text file's path instead")
    p.add_argument("--json", action="store_true", help="print the resolution (status, locator, text) as JSON")
    a = p.parse_args(argv)
    m = Matter.find(a.matter)
    r = locators.resolve(m, a.locator)
    if a.json:
        out(dict(r))
        return
    if r["status"] == "broken":
        raise locators.LocatorError(f"broken locator: {r['error']}")
    if r["status"] != "exact":
        print(json.dumps({"status": r["status"], "loc": r["loc"], "notes": r.get("notes")}), file=sys.stderr)
    loc = r["loc"]
    if a.path or "text" not in r:
        layer = m.state.layer(loc["layer"]) if loc.get("layer") else m.state.primary_layer(m.state.documents[loc["doc"]])
        if layer is None:
            raise locators.LocatorError("document has no text layer")
        if a.path:
            print(m.objects.path(layer.text))
        else:
            sys.stdout.write(m.objects.get_text(layer.text))
        return
    sys.stdout.write(r["text"])
    if not r["text"].endswith("\n"):
        sys.stdout.write("\n")


# -- idx ---------------------------------------------------------------------------------

@run
def idx(argv=None):
    from .index import all_names, open_index
    p = parser("idx", "Manage indexes in workspace/.adhoc/indexes/.")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, h in (("status", "settings, cursor, coverage and size"), ("update", "catch up with the change feed"), ("verify", "compare with the store"),
                    ("rebuild", "delete and rebuild")):
        s = sub.add_parser(name, help=h)
        s.add_argument("names", nargs="*")
        if name == "update":
            s.add_argument("--background", action="store_true")
        if name == "verify":
            s.add_argument("--repair", action="store_true")
    s = sub.add_parser("changes", help="print the change feed")
    s.add_argument("--since", help='cursor JSON, e.g. \'{"store": "ev_…"}\'')
    s = sub.add_parser("new", help="register a custom index updated by your own command")
    s.add_argument("name")
    s.add_argument("--updater", required=True, help="command that reads change events on stdin")
    s.add_argument("--kind", default="custom")
    s.add_argument("--settings", default="{}", help="JSON")
    a = p.parse_args(argv)
    m = Matter.find(a.matter)

    if a.cmd == "changes":
        for ev in m.changes(json.loads(a.since) if a.since else None):
            out(ev)
        return
    if a.cmd == "new":
        d = m.indexes_dir / a.name
        d.mkdir(parents=True, exist_ok=False)
        (d / "manifest.json").write_text(json.dumps(
            {"type": "index", "v": 1, "name": a.name, "kind": a.kind, "settings": json.loads(a.settings),
             "cursor": {"store": None, "workspace": None}, "updater": a.updater}, indent=2) + "\n")
        out({"created": str(d)})
        return
    names = getattr(a, "names", None) or all_names(m)
    for name in names:
        if a.cmd == "status":
            ix = open_index(m, name, create=False) if (m.indexes_dir / name).exists() else None
            if ix is None:
                out({"name": name, "exists": False})
                continue
            st = ix.status()
            if hasattr(ix, "indexed"):
                from .search import Options, Searcher
                st["coverage"] = Searcher(m, Options()).coverage(ix)
            out(st)
        elif a.cmd == "update":
            if a.background:
                from .search import start_background_update
                out({"name": name, "background": start_background_update(m, name)})
            else:
                ix = open_index(m, name)
                out({"name": name, "applied": ix.update(), "cursor": ix.cursor()})
        elif a.cmd == "verify":
            out(open_index(m, name).verify(repair=a.repair))
        elif a.cmd == "rebuild":
            shutil.rmtree(m.indexes_dir / name, ignore_errors=True)
            ix = open_index(m, name)
            out({"name": name, "applied": ix.update(), "cursor": ix.cursor()})


# -- search ------------------------------------------------------------------------------

SEARCH_HELP = """\
search "<query>" [--index keyword|semantic|hybrid]   (keyword is the default)
search grep <regex>                                  regex over layer text, no index
search similar <locator>                             semantic neighbours

Output: a JSON header (query, index, as_of, exhaustive, coverage, matches),
then one line per document (--by doc) or per hit (--by hit). Each hit has a
durable locator with its quote read from the layer.

Query language:
""" + __import__("adhocdocs.query", fromlist=["x"]).__doc__.split("\n", 1)[1]


@run
def search(argv=None):
    from .search import Options, Searcher, to_list
    argv = sys.argv[1:] if argv is None else argv
    mode = "query"
    if argv and argv[0] in ("grep", "similar"):
        mode, argv = argv[0], argv[1:]
    p = parser("search", SEARCH_HELP)
    p.add_argument("q", metavar="query|regex|locator")
    p.add_argument("--index", choices=["keyword", "semantic", "hybrid"], default="keyword")
    p.add_argument("--within", help="list file, or - for locators on stdin")
    p.add_argument("--where", help="field filters, in query syntax")
    p.add_argument("--tag", action="append", default=[], help="tag filter; -name for absent")
    p.add_argument("--layers", default="primary", help="primary, all, or a view (native, ocr, …)")
    p.add_argument("--in", dest="in_kinds", action="append", default=[], help="segment kinds: body, quoted, header, …")
    p.add_argument("--by", choices=["doc", "hit"], default="doc")
    p.add_argument("--limit", type=int, help="ranked searches; keyword and grep return everything by default")
    p.add_argument("--list", dest="list_file", help="also write the results as a list file")
    p.add_argument("--explain", action="store_true", help="print the parsed query and stop")
    p.add_argument("-i", "--ignore-case", action="store_true", help="grep only")
    # "-tag:x" and "-word" are query syntax (and "-name" a tag filter), not options
    argv = [" " + x if x.startswith("-") and not x.startswith("--") and x not in ("-", "-i", "-h") else x for x in argv]
    a = p.parse_args(argv)
    a.q = a.q.strip()
    a.tag = [t.strip() for t in a.tag]
    m = Matter.find(a.matter)

    within = None
    if a.within:
        if a.within == "-":
            within = [locators.parse(line.strip(), m.objects) for line in sys.stdin if line.strip()]
        else:
            _, items = lists.read(a.within)
            within = []
            for it in items:
                within.append(locators.parse(it["loc"], m.objects))
                within += [locators.parse(c["loc"], m.objects) for c in it.get("children", [])]
    opts = Options(layers=a.layers, in_kinds=a.in_kinds, within=within, where=a.where, tags=a.tag,
                   by=a.by, limit=a.limit, ignore_case=a.ignore_case)
    s = Searcher(m, opts)

    if a.explain:
        from . import query as Q
        node = Q.parse(a.q)
        out({"parsed": Q.to_text(node), "fts": sorted({l.fts() for l, _ in Q.leaves(node)})})
        return
    if mode == "grep":
        header, items = s.grep(a.q)
    elif mode == "similar":
        header, items = s.similar(a.q)
    elif a.index == "semantic":
        header, items = s.semantic(a.q)
    elif a.index == "hybrid":
        header, items = s.hybrid(a.q)
    else:
        header, items = s.keyword(a.q)
    header, items = s.finish(header, items)
    out(header)
    for it in items:
        out(it)
    if a.list_file:
        lists.write(a.list_file, *(lambda rows: (rows[0], rows[1:]))(to_list(header, items, a.by)))


# -- list ----------------------------------------------------------------------------------

@run
def list_(argv=None):
    p = parser("list", "Make and change list files (*.list.jsonl).")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("new", help="make a list from locators on stdin, one per line")
    s.add_argument("out")
    s.add_argument("--title", required=True)
    s.add_argument("--columns", nargs="*")
    s = sub.add_parser("cat", help="print the items of lists")
    s.add_argument("files", nargs="+")
    for name in ("sort", "group"):
        s = sub.add_parser(name, help=f"{name} by a document field (or a list field, for sort)")
        s.add_argument("inp")
        s.add_argument("out")
        s.add_argument("--by", required=True)
        if name == "sort":
            s.add_argument("--desc", action="store_true")
    s = sub.add_parser("filter", help="keep items with (or, with -name, without) tags")
    s.add_argument("inp")
    s.add_argument("out")
    s.add_argument("--tag", action="append", required=True)
    argv = sys.argv[1:] if argv is None else argv
    argv = [" " + x if x.startswith("-") and not x.startswith("--") and x != "-h" else x for x in argv]
    a = p.parse_args(argv)
    a.tag = [t.strip() for t in getattr(a, "tag", None) or []]

    if a.cmd == "new":
        header, items = lists.new(a.title, [line.strip() for line in sys.stdin if line.strip()], a.columns)
        lists.write(a.out, header, items)
        out({"written": a.out, "items": len(items)})
        return
    if a.cmd == "cat":
        for f in a.files:
            for it in lists.read(f)[1]:
                out(it)
        return
    m = Matter.find(a.matter)
    header, items = lists.read(a.inp)
    if a.cmd in ("sort", "group"):
        from .index import open_index
        ix = open_index(m, "fields")
        ix.update()
        fields = ix.rows()
        items = lists.sort(items, fields, a.by, a.desc) if a.cmd == "sort" else lists.group(items, fields, a.by)
    else:
        from .annotations import current_tags
        tags = current_tags(m)
        for t in a.tag:
            neg, name = t.startswith("-"), t.lstrip("-")
            items = [it for it in items if (name in tags.get(lists.doc_of(it), ())) != neg]
    lists.write(a.out, header, items)
    out({"written": a.out, "items": len(items)})


def main():
    """``python -m adhocdocs.cli <command> …``"""
    cmds = {"adhoc": adhoc, "idx": idx, "search": search, "layers": layers, "text": text, "list": list_}
    if len(sys.argv) < 2 or sys.argv[1] not in cmds:
        print(f"usage: python -m adhocdocs.cli {{{','.join(cmds)}}} …", file=sys.stderr)
        sys.exit(2)
    cmds[sys.argv[1]](sys.argv[2:])


if __name__ == "__main__":
    main()
