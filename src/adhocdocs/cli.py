"""Command-line entry points. Every command prints JSON (or JSON Lines).

The matter is found from ``--matter``, ``$ADHOC_MATTER``, or the nearest
directory above the current one that has a ``store/``.
"""

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

from . import annapi, annotations, lists, locators, view as viewmod
from .matter import Matter, MatterNotFound


def out(obj):
    print(json.dumps(obj, ensure_ascii=False))


def parser(prog, doc):
    p = argparse.ArgumentParser(prog=prog, description=doc, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--matter", help="matter directory (default: $ADHOC_MATTER or the enclosing matter)")
    return p


def matter_everywhere(sub):
    """Accept --matter after the subcommand too, not only before it."""
    for sp in sub.choices.values():
        sp.add_argument("--matter", default=argparse.SUPPRESS, help=argparse.SUPPRESS)


def run(fn):
    """Errors as one JSON line on stderr and exit status 1, not tracebacks."""
    def wrapper(argv=None):
        try:
            return fn(argv)
        except annotations.Refused as e:
            print(json.dumps({"refused": str(e)}), file=sys.stderr)
            sys.exit(1)
        except (MatterNotFound, locators.LocatorError, annapi.Unavailable, viewmod.NotRunning, viewmod.ViewerError,
                ValueError, KeyError) as e:
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
    s = sub.add_parser("open", help="start a session for a matter: the container, the viewer and the annotation API")
    s.add_argument("path", nargs="?", help="the matter (default: --matter, $ADHOC_MATTER or the enclosing matter)")
    s.add_argument("--no-viewer", action="store_true")
    s.add_argument("--user", default=os.environ.get("USER", "user"), help="who the viewer writes tags and notes as")
    s.add_argument("command", nargs="*", help="run this instead of claude (after --)")
    s = sub.add_parser("image", help="build the session image on this machine")
    s.add_argument("action", choices=["build"])
    s.add_argument("--no-cache", action="store_true")
    sub.add_parser("status", help="inside a session: the matter, mounts, network and services")
    s = sub.add_parser("view", help="start the document viewer for this matter")
    s.add_argument("--user", default=os.environ.get("USER", "user"), help="who the viewer writes tags and notes as")
    s.add_argument("--wait", action="store_true", help="run in the foreground")
    s = sub.add_parser("annotate-as-user", help=argparse.SUPPRESS)
    s.add_argument("--user", required=True)
    s = sub.add_parser("ann-api", help="serve the annotation API (writes stamped `claude`) on a Unix socket")
    s.add_argument("--socket", help="default: $ADHOC_ANN_SOCKET or <matter>/run/annotations.sock")
    matter_everywhere(sub)
    a = p.parse_args(argv)

    if a.cmd == "init":
        m = Matter.create(a.path)
        out({"matter": str(m.root)})
        return
    if a.cmd in ("open", "image", "status"):
        from . import container
        try:
            if a.cmd == "status":
                out(container.status())
            elif a.cmd == "image":
                container.build_image(a.no_cache)
            else:
                sys.exit(container.open_session(Matter.find(a.path or a.matter), a.user, not a.no_viewer, a.command))
        except container.ContainerError as e:
            print(json.dumps({"error": str(e)}), file=sys.stderr)
            sys.exit(1)
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
    elif a.cmd == "view":
        out({"viewer": viewmod.launch(m, a.user, a.wait), "socket": str(viewmod.socket_path(m))})
    elif a.cmd == "annotate-as-user":
        # The viewer's write path for the user's own tags and notes (host only).
        out({"events": annotations.write(m, f"user:{a.user}", json.loads(sys.stdin.read()))})
    elif a.cmd == "ann-api":
        path = a.socket or annapi.socket_path(m)
        print(json.dumps({"serving": str(path)}), file=sys.stderr, flush=True)
        try:
            annapi.serve(m, path)
        except KeyboardInterrupt:
            pass


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
    if a.locator.startswith("bates:"):
        from .produce import resolve_bates
        for hit in resolve_bates(m, a.locator):
            r = locators.resolve(m, hit["loc"])
            out({**hit, "status": r["status"], **({"text": r["text"]} if a.json and "text" in r else {})})
        return
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
    matter_everywhere(sub)
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
    matter_everywhere(sub)
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


# -- ann ------------------------------------------------------------------------------------

ANN_HELP = """\
Tags and notes. Reads go straight to the annotation log; writes go through
the annotation API, which records them as Claude's. Claude can't edit or
remove the user's tags, notes or tag definitions.

Pass --requested-by user when the user asked for the change.
Tags must be defined (define-tag) before use. A tag in an exclusive --group
replaces the group's other tag on the same target.
"""


def _family(state, doc):
    top = doc
    parents = {c: p for p, cs in state.children.items() for c in cs}
    while top in parents:
        top = parents[top]
    out, todo = [], [top]
    while todo:
        d = todo.pop(0)
        out.append(d)
        todo += state.children.get(d, [])
    return [d for d in out if d not in state.withdrawn]


def _targets(m, locs, family=False, dupes=False):
    state = m.state
    out = []
    for s in locs:
        loc = locators.parse(s if s.lstrip().startswith("{") else "doc:" + s.removeprefix("doc:"), m.objects)
        extra = []
        if family:
            extra += _family(state, loc["doc"])
        if dupes:
            content = state.documents.get(loc["doc"])
            extra += [d for d in state.live_documents() if state.documents[d] == content]
        if extra and loc.get("select"):
            raise ValueError("--family and --dupes apply to whole documents")
        out += [{"doc": d} for d in dict.fromkeys(extra)] if extra else [loc]
    return out


@run
def ann(argv=None):
    p = parser("ann", ANN_HELP)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("tags", help="the tag vocabulary with counts, or the tags on --target")
    s.add_argument("--target", help="a locator; a whole document includes tags on ranges in it")
    s = sub.add_parser("notes", help="notes, with replies after their parent")
    s.add_argument("--target")
    s.add_argument("--by", help="user, claude, or user:<id>")
    s = sub.add_parser("find", help="documents by tags: --tag hot --tag -privileged")
    s.add_argument("--tag", action="append", required=True)
    s.add_argument("--list", dest="list_file", help="also write a list file")
    s = sub.add_parser("log", help="the event log")
    s.add_argument("--since", help="event ID")
    s.add_argument("--author", help="user, claude, or user:<id>")
    s = sub.add_parser("check", help="check a note's references and quotations (note ID, or - for a body on stdin)")
    s.add_argument("note")
    for name in ("tag", "untag"):
        s = sub.add_parser(name)
        s.add_argument("tag")
        s.add_argument("locators", nargs="+")
        s.add_argument("--family", action="store_true", help="every document in each one's family")
        s.add_argument("--dupes", action="store_true", help="every exact duplicate of each one")
        s.add_argument("--requested-by")
    s = sub.add_parser("note", help="add a note (body on stdin, or --body)")
    s.add_argument("target", nargs="?")
    s.add_argument("--reply-to")
    s.add_argument("--body")
    s.add_argument("--requested-by")
    s = sub.add_parser("edit-note", help="replace a note's body (stdin, or --body)")
    s.add_argument("note")
    s.add_argument("--body")
    s.add_argument("--requested-by")
    s = sub.add_parser("delete-note")
    s.add_argument("note")
    s.add_argument("--requested-by")
    for name in ("define-tag", "edit-tag"):
        s = sub.add_parser(name)
        s.add_argument("tag")
        s.add_argument("--color")
        s.add_argument("--hotkey")
        s.add_argument("--group", help="exclusive group, e.g. responsiveness")
        s.add_argument("--description", help="the review criterion")
        s.add_argument("--requested-by")
    argv = sys.argv[1:] if argv is None else argv
    argv = [" " + x if x.startswith("-") and not x.startswith("--") and x not in ("-", "-h") else x for x in argv]
    matter_everywhere(sub)
    a = p.parse_args(argv)
    if getattr(a, "tag", None) and isinstance(a.tag, list):
        a.tag = [t.strip() for t in a.tag]
    m = Matter.find(a.matter)

    def who(spec, author):
        return spec is None or author == spec or (spec == "user" and annotations.is_user(author))

    if a.cmd == "tags":
        an = annotations.Annotations.load(m)
        if a.target:
            t = _targets(m, [a.target])[0]
            for app in an.applications():
                if (annotations.target_key(app["target"]) == annotations.target_key(t)
                        or (set(t) == {"doc"} and app["target"]["doc"] == t["doc"])):
                    out(app)
            return
        counts = {}
        for app in an.applications():
            counts[app["tag"]] = counts.get(app["tag"], 0) + 1
        for name in sorted(set(an.definitions) | set(counts)):
            out({**an.definitions.get(name, {"tag": name, "undefined": True}), "count": counts.get(name, 0)})
    elif a.cmd == "notes":
        an = annotations.Annotations.load(m)
        t = _targets(m, [a.target])[0] if a.target else None
        notes = [n for n in an.notes.values() if who(a.by, n["by"])
                 and (t is None or annotations.target_key(n["target"]) == annotations.target_key(t)
                      or (set(t) == {"doc"} and n["target"]["doc"] == t["doc"]))]
        ids = {n["note"] for n in notes}
        def thread(n, depth):
            out({**n, "depth": depth})
            for r in notes:
                if r["parent"] == n["note"]:
                    thread(r, depth + 1)
        for n in notes:
            if n["parent"] not in ids:
                thread(n, 0)
    elif a.cmd == "find":
        tags = annotations.current_tags(m)
        docs = [d for d in m.state.live_documents()
                if all((t.lstrip("-") in tags.get(d, ())) != t.startswith("-") for t in a.tag)]
        for d in docs:
            out({"loc": "doc:" + d, "tags": sorted(tags.get(d, ()))})
        if a.list_file:
            lists.write(a.list_file, *lists.new(" ".join(a.tag), ["doc:" + d for d in docs],
                                                query=" ".join(f"tag:{t}" for t in a.tag)))
    elif a.cmd == "log":
        for ev in m.annotation_log.read(a.since):
            if who(a.author, ev["author"]):
                out(ev)
    elif a.cmd == "check":
        if a.note == "-":
            body = sys.stdin.read()
        else:
            n = annotations.Annotations.load(m).notes.get(a.note)
            if n is None:
                raise KeyError(f"no note {a.note}")
            body = n["body"]
        from . import literate
        for r in literate.check(m, body):
            out(r)
    else:
        req = {"op": a.cmd, "requested_by": getattr(a, "requested_by", None)}
        if a.cmd in ("tag", "untag"):
            results = []
            for t in _targets(m, a.locators, a.family, a.dupes):
                results.append(annapi.request(m, {**req, "tag": a.tag, "target": t}))
            out({"events": [e for r in results for e in r["events"]]})
            return
        if a.cmd in ("note", "edit-note"):
            req["body"] = a.body if a.body is not None else sys.stdin.read()
        if a.cmd == "note":
            if a.reply_to:
                req.update(op="reply", parent=a.reply_to)
            elif a.target:
                req["target"] = _targets(m, [a.target])[0]
            else:
                raise ValueError("note needs a target locator or --reply-to")
        elif a.cmd in ("edit-note", "delete-note"):
            req["note"] = a.note
        elif a.cmd in ("define-tag", "edit-tag"):
            req.update(tag=a.tag, color=a.color, hotkey=a.hotkey, group=a.group, description=a.description)
        out(annapi.request(m, req))


# -- page ----------------------------------------------------------------------------------

PAGE_HELP = """\
Literate pages: Markdown whose quotations are references to document text.

  page ref add page.md <id> <locator>...   add a reference (several locators: an elided quotation)
  page check page.md                       resolve every reference and lint
  page refs update page.md                 rewrite re-anchored references
  page render page.md [-o out.html]        static HTML (the viewer fills in quotations)
  page export page.md --format html|docx|pdf -o out

Format:
""" + __import__("adhocdocs.literate", fromlist=["x"]).__doc__.split("\n", 1)[1]


@run
def page(argv=None):
    from . import pages
    argv = sys.argv[1:] if argv is None else argv
    if argv[:2] == ["ref", "add"]:
        argv = ["ref-add"] + argv[2:]
    elif argv[:2] == ["refs", "update"]:
        argv = ["refs-update"] + argv[2:]
    p = parser("page", PAGE_HELP)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("check")
    s.add_argument("file")
    s.add_argument("--all", action="store_true", help="also print references that are fine")
    s = sub.add_parser("render")
    s.add_argument("file")
    s.add_argument("-o", "--out", help="default: stdout")
    s = sub.add_parser("export")
    s.add_argument("file")
    s.add_argument("--format", choices=["html", "docx", "pdf"], required=True)
    s.add_argument("-o", "--out", required=True)
    s.add_argument("--allow-reanchored", action="store_true")
    s = sub.add_parser("ref-add", help="page ref add <file> <id> <locator>...")
    s.add_argument("file")
    s.add_argument("id")
    s.add_argument("locators", nargs="+", help="string or JSON locators (e.g. a search hit's loc)")
    s.add_argument("--replace", action="store_true")
    s = sub.add_parser("refs-update", help="page refs update <file>")
    s.add_argument("file")
    matter_everywhere(sub)
    a = p.parse_args(argv)
    m = Matter.find(a.matter)
    try:
        if a.cmd == "check":
            results = pages.Page(m, a.file).check()
            for r in results if a.all else [r for r in results if r in __import__("adhocdocs.literate", fromlist=["x"]).problems(results)]:
                out(r)
            if any(r.get("status") == "broken" or r.get("lint") in ("missing-ref", "bad-ref", "duplicate-ref") for r in results):
                sys.exit(1)
        elif a.cmd == "render":
            html_doc, results = pages.render(pages.Page(m, a.file))
            if a.out:
                Path(a.out).write_text(html_doc)
                out({"written": a.out, "problems": len([r for r in results if "lint" in r or r.get("status") != "exact"])})
            else:
                sys.stdout.write(html_doc)
        elif a.cmd == "export":
            out(pages.export(pages.Page(m, a.file), a.format, Path(a.out), a.allow_reanchored))
        elif a.cmd == "ref-add":
            out(pages.add_ref(m, a.file, a.id, a.locators, a.replace))
        elif a.cmd == "refs-update":
            for c in pages.update_refs(m, a.file):
                out(c)
    except pages.PageError as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)


# -- view ------------------------------------------------------------------------------------

VIEW_HELP = """\
Drive the document viewer. The viewer runs on the host (the user starts it);
this only talks to it. Locators are string or JSON; hash prefixes work.

Keys in the viewer: j/k move, Enter open, ]/[ next/previous document,
Space page, m mark, t tag palette (Shift+Enter tags the family, Ctrl+Enter
the duplicates), N note, tag hotkeys, v layer, f mode, Tab pane, Alt-drag
a region on a page, ? help.

Services: a pane's <form action="svc:NAME"> is answered immediately by
`view serve NAME -- cmd …` (run it in the background), without waiting for
the next prompt. cmd gets {"form", "selection", "pane"} as JSON on stdin and
prints the pane's new HTML.
"""


@run
def view(argv=None):
    p = parser("view", VIEW_HELP)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("open", help="open a locator, scrolled to and briefly highlighted")
    s.add_argument("loc")
    s.add_argument("--pane")
    s.add_argument("--new-pane", action="store_true")
    s.add_argument("--layer", dest="view_", help="layer view: native, ocr, transcript, …")
    s.add_argument("--mode", choices=["page", "flow", "text"])
    s = sub.add_parser("highlight", help="mark a place for the user; stays until cleared")
    s.add_argument("loc")
    s.add_argument("--label")
    s.add_argument("--pane")
    s = sub.add_parser("clear-highlights")
    s.add_argument("--pane")
    s = sub.add_parser("sequence", help="give a document pane an ordered list to step through")
    s.add_argument("locs", nargs="*", help="locators, a list file (--list), or - for stdin")
    s.add_argument("--list", dest="list_file")
    s.add_argument("--pane")
    s = sub.add_parser("pane", help="show static HTML (a file or a directory bundle with index.html)")
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--new", action="store_true")
    g.add_argument("--update", metavar="PANE")
    s.add_argument("path")
    s.add_argument("--title")
    s.add_argument("--link", metavar="DOC_PANE", help="a document pane that follows the selection")
    s.add_argument("--preview-new", action="store_true", help="make a new linked document pane")
    s = sub.add_parser("list", help="show a list file, with a linked preview pane")
    s.add_argument("file")
    s.add_argument("--pane", help="replace this list pane's content")
    s.add_argument("--preview", metavar="DOC_PANE")
    s = sub.add_parser("close")
    s.add_argument("pane")
    s = sub.add_parser("layout")
    s.add_argument("layout", choices=["single", "split-v", "split-h", "grid"])
    sub.add_parser("state", help="windows, panes, what's visible, selections, marks, form values")
    sub.add_parser("selection", help="the focused pane's selection and marks")
    s = sub.add_parser("history")
    s.add_argument("--limit", type=int, default=50)
    s = sub.add_parser("clear-marks")
    s.add_argument("--pane")
    s = sub.add_parser("serve", help="answer a pane's <form action=\"svc:NAME\"> with a command (run in the background)")
    s.add_argument("name")
    s.add_argument("command", nargs="+", help="after --: gets {form, selection, pane} as JSON on stdin, prints the new HTML")
    matter_everywhere(sub)
    a = p.parse_args(argv)
    m = Matter.find(a.matter)

    def loc(x):
        if x.startswith("bates:"):            # the first document the Bates range touches
            from .produce import resolve_bates
            return locators.format(resolve_bates(m, x)[0]["loc"])
        return locators.format(locators.parse(x if x.lstrip().startswith("{") else "doc:" + x.removeprefix("doc:"), m.objects))

    if a.cmd == "serve":
        viewmod.serve(m, a.name, a.command)
        return
    c = {"cmd": a.cmd}
    if a.cmd in ("open", "highlight"):
        c["loc"] = loc(a.loc)
        c.update(pane=a.pane, label=getattr(a, "label", None))
        if a.cmd == "open":
            c.update(new_pane=a.new_pane, view=a.view_, mode=a.mode)
    elif a.cmd == "sequence":
        if a.list_file:
            _, items = lists.read(a.list_file)
            raw = [it["loc"] for it in items]
        elif a.locs == ["-"]:
            raw = [line.strip() for line in sys.stdin if line.strip()]
        else:
            raw = a.locs
        c.update(locs=[loc(x if isinstance(x, str) else json.dumps(x)) for x in raw], pane=a.pane)
    elif a.cmd == "pane":
        c.update(files=viewmod.files_of(Path(a.path)), title=a.title, link=a.link, preview_new=a.preview_new, update=a.update)
    elif a.cmd == "list":
        files, sequence = viewmod.list_html(m, Path(a.file))
        header, _ = lists.read(a.file)
        c = {"cmd": "pane", "kind": "list", "files": files, "sequence": sequence,
             "title": header.get("title") or Path(a.file).stem, "update": a.pane,
             "link": a.preview, "preview_new": not a.preview and not a.pane}
    elif a.cmd in ("close",):
        c["pane"] = a.pane
    elif a.cmd == "layout":
        c["layout"] = a.layout
    elif a.cmd == "history":
        c["limit"] = a.limit
    elif a.cmd in ("clear-highlights", "clear-marks"):
        c["pane"] = a.pane
    out(viewmod.command(m, {k: v for k, v in c.items() if v is not None and v is not False}))


# -- analyze ---------------------------------------------------------------------------------

@run
def analyze(argv=None):
    from . import analysis
    p = parser("analyze", analysis.__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("threads", help="email threads of two or more messages, oldest first")
    s.add_argument("--doc", help="only the thread containing this document")
    s.add_argument("--by-subject", action="store_true", help="join emails without threading headers by subject")
    s.add_argument("--list", dest="list_file", help="also write a list file, grouped by thread")
    s = sub.add_parser("near", help="documents similar to one document")
    s.add_argument("doc")
    s.add_argument("--threshold", type=float, default=0.5)
    s = sub.add_parser("groups", help="clusters of near-duplicate texts")
    s.add_argument("--threshold", type=float, default=0.8)
    s = sub.add_parser("people", help="a cast of characters from email headers")
    s.add_argument("--mentions", action="store_true", help="also count the documents that name each person")
    s = sub.add_parser("redline", help="word-level changes from document A to document B")
    s.add_argument("a")
    s.add_argument("b")
    s.add_argument("--html", help="also write static HTML for a viewer pane (view pane --new FILE)")
    matter_everywhere(sub)
    a = p.parse_args(argv)
    m = Matter.find(a.matter)

    def doc(x):
        return locators.parse("doc:" + x.removeprefix("doc:"), m.objects)["doc"] if not x.lstrip().startswith("{") \
            else locators.parse(x, m.objects)["doc"]

    if a.cmd == "threads":
        ts = analysis.threads(m, a.by_subject)
        if a.doc:
            d = "doc:" + doc(a.doc)
            ts = [t for t in ts if any(d == x["loc"] or d in x["copies"] for x in t["messages"])]
        for t in ts:
            out(t)
        if a.list_file:
            items = []
            for t in ts:
                group = f"{t['subject'] or '(no subject)'} · {t['first'][:10] if t['first'] else ''}"
                items += [{"loc": x["loc"], "group": group, "fields": {"depth": x["depth"]}} for x in t["messages"]]
            lists.write(a.list_file, {"type": "list", "v": 1, "title": "Email threads", "made_by": "analyze threads",
                                      "columns": ["date", "from", "subject", "depth", "tags"]}, items)
    elif a.cmd == "near":
        out(analysis.near(m, doc(a.doc), a.threshold))
    elif a.cmd == "groups":
        for g in analysis.groups(m, a.threshold):
            out(g)
    elif a.cmd == "people":
        for p_ in analysis.people(m, a.mentions):
            out(p_)
    elif a.cmd == "redline":
        header, changes = analysis.redline(m, doc(a.a), doc(a.b))
        out(header)
        for c in changes:
            out(c)
        if a.html:
            Path(a.html).write_text(analysis.redline_html(m, doc(a.a), doc(a.b)))


# -- produce ---------------------------------------------------------------------------------

@run
def produce(argv=None):
    from . import produce as prod
    p = parser("produce", prod.__doc__)
    p.add_argument("list_file", help="the documents to produce, in order")
    p.add_argument("--name", required=True, help="e.g. vol001")
    p.add_argument("--prefix", required=True, help="Bates prefix, e.g. HFS")
    p.add_argument("--start", type=int, default=1)
    p.add_argument("--digits", type=int, default=6)
    p.add_argument("--legend", help='e.g. "CONFIDENTIAL"')
    p.add_argument("--no-families", action="store_true", help="don't add each document's attachments after it")
    p.add_argument("--withhold", action="append", help="tag that withholds a document (default: privileged)")
    p.add_argument("--redact", default="redact", help="tags starting with this redact what they're on (default: redact)")
    a = p.parse_args(argv)
    m = Matter.find(a.matter)
    try:
        out(prod.produce(m, Path(a.list_file), a.name, a.prefix, a.start, a.digits, a.legend, not a.no_families,
                         tuple(a.withhold or ["privileged"]), a.redact))
    except prod.ProductionError as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)


def main():
    """``python -m adhocdocs.cli <command> …``"""
    cmds = {"adhoc": adhoc, "idx": idx, "search": search, "layers": layers, "text": text, "list": list_, "ann": ann, "page": page, "view": view, "analyze": analyze, "produce": produce}
    if len(sys.argv) < 2 or sys.argv[1] not in cmds:
        print(f"usage: python -m adhocdocs.cli {{{','.join(cmds)}}} …", file=sys.stderr)
        sys.exit(2)
    cmds[sys.argv[1]](sys.argv[2:])


if __name__ == "__main__":
    main()
