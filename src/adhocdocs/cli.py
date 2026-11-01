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

from . import annapi, annotations, entities, lists, locators, view as viewmod
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
        except Exception as e:
            # sync's errors (adhocdocs.peer, pool, invite, hub, signing, work), said plainly
            if type(e).__name__ in SYNC_ERRORS:
                print(json.dumps({"error": str(e)}), file=sys.stderr)
                sys.exit(1)
            raise
    return wrapper


SYNC_ERRORS = {"SyncError", "PoolError", "InviteError", "HubError", "BadLog", "PermissionError", "ConnectionRefusedError",
               "TimeoutError", "SSLError"}


# -- adhoc: host-side matter administration -------------------------------------------

@run
def adhoc(argv=None):
    p = parser("adhoc", "Matter administration (host side).")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("init", help="create an empty matter")
    s.add_argument("path")
    s = sub.add_parser("setup", help="check prerequisites, then build the viewer and the session image")
    s.add_argument("--check", action="store_true", help="only check the prerequisites")
    s.add_argument("--no-viewer", action="store_true", help="don't build the viewer")
    s.add_argument("--no-image", action="store_true", help="don't build the session image")
    s.add_argument("--no-cache", action="store_true", help="rebuild the image from scratch (e.g. for a newer Claude Code)")
    s = sub.add_parser("ingest", help="add files or directories to the matter",
                       description="Add files or directories to the matter: give paths and --collection, "
                                   "or --manifest for several collections at once.")
    s.add_argument("paths", nargs="*")
    s.add_argument("--custodian", help="custodian ID, e.g. smith-j (part of document identity)")
    s.add_argument("--collection", help="collection name, e.g. smith-laptop-2026-03")
    s.add_argument("--manifest", help="a TOML file of [[collection]] tables (name, custodian, paths), "
                                      "e.g. corpus/manifest.toml; see adhocdocs.ingest.load_manifest")
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
    s = sub.add_parser("audit", help="the audit log: sessions, events, or a check that it is intact")
    s.add_argument("action", choices=["sessions", "show", "verify"])
    s.add_argument("--session", help="show: only this session")
    s.add_argument("--kind", action="append", help="show: only these kinds (session-start, session-end, prompt, "
                                                   "tool, connection, claude); repeatable")
    s.add_argument("--full", action="store_true", help="show: whole tool results instead of previews")
    s = sub.add_parser("publish", help="publish a workspace file or directory as yours, for the matter's other "
                                       "installations (see `work --help`)")
    s.add_argument("path")
    s.add_argument("--user", required=True)
    s.add_argument("--merge", action="store_true", help="revise every current version: resolves a fork")
    s.add_argument("--revises", action="append")
    s = sub.add_parser("retire", help="take published work out of work/ (the history stays)")
    s.add_argument("name")
    s.add_argument("--user", required=True)
    s = sub.add_parser("sync", help="syncing with this matter's other installations (SYNC.md)",
                       description=SYNC_HELP, formatter_class=argparse.RawDescriptionHelpFormatter)
    ss = s.add_subparsers(dest="action", required=True)
    ss.add_parser("status", help="this matter's ID, this installation's origin, the members, and the events held "
                                 "from each origin")
    ss.add_parser("conflicts", help="events from different installations that clashed, how each was resolved, "
                                    "and published work that forked")
    x = ss.add_parser("invite", help="an invitation for another installation to join (founds the pool if needed)")
    x.add_argument("--user", required=True)
    x.add_argument("--address", action="append", default=[],
                   help="host:port where this installation listens (adhoc sync serve); repeatable")
    x.add_argument("--role", choices=["member", "hub"], default="member")
    x.add_argument("--hours", type=float, default=72, help="how long the invitation works (default 72)")
    x.add_argument("--name", help="this installation's name, if this founds the pool (default: the host name)")
    x.add_argument("--same-person", action="store_true",
                   help="the one joining is yours too (your desktop, say): it may write as --user")
    x = ss.add_parser("join", help="join a matter's pool with an invitation, into a new matter directory")
    x.add_argument("code")
    x.add_argument("path", help="a new, empty directory for the matter")
    x.add_argument("--name", help="this installation's name (default: the host name)")
    x.add_argument("--address", action="append", default=[], help="host:port where this one will listen, if it will")
    x = ss.add_parser("serve", help="listen for the pool's other members (runs until stopped)")
    x.add_argument("--listen", default=f"0.0.0.0:7480", help="host:port to listen on (default 0.0.0.0:7480)")
    x.add_argument("--advertise", action="append", default=[],
                   help="host:port the others should use to reach this one, recorded in the pool; repeatable")
    x.add_argument("--user", help="who is advertising the address (needed with --advertise)")
    x.add_argument("--every", type=float, default=5, help="sync with every member at least every N minutes (default 5)")
    x.add_argument("--no-watch", action="store_true", help="only serve: don't sync as things change")
    x.add_argument("--no-lan", action="store_true", help="don't announce this, or look for members, on the local network")
    x = ss.add_parser("watch", help="sync as things change, here or on the members it can reach (runs until stopped)")
    x.add_argument("--every", type=float, default=5, help="sync with every member at least every N minutes (default 5)")
    x.add_argument("--no-lan", action="store_true", help="don't look for members on the local network")
    ss.add_parser("now", help="sync with every member that says where it listens")
    x = ss.add_parser("push", help="put this matter on a hub (with a code from its administrator), then sync")
    x.add_argument("code", help="the hub code from adhoc hub enroll")
    x.add_argument("--user", required=True)
    ss.add_parser("members", help="the pool's members")
    x = ss.add_parser("vouch", help="say another member writes for you too (from an installation that writes for you)")
    x.add_argument("origin")
    x.add_argument("--user", required=True)
    x = ss.add_parser("remove", help="remove a member: no one syncs with it again (it keeps what it has)")
    x.add_argument("origin")
    x.add_argument("--user", required=True)
    s = sub.add_parser("hub", help="run a hub: an always-on member that holds many matters (SYNC.md)",
                       description=HUB_HELP, formatter_class=argparse.RawDescriptionHelpFormatter)
    hs = s.add_subparsers(dest="action", required=True)
    x = hs.add_parser("init", help="make a hub in a directory")
    x.add_argument("dir")
    x.add_argument("--name", required=True, help="what members will see, e.g. \"Firm hub\"")
    x.add_argument("--address", action="append", default=[], help="host:port members reach it at; repeatable")
    x = hs.add_parser("enroll", help="a hub code for a person, to push matters here")
    x.add_argument("dir")
    x.add_argument("--person", required=True)
    x = hs.add_parser("revoke", help="stop a person's hub codes working (matters already here stay)")
    x.add_argument("dir")
    x.add_argument("--person", required=True)
    x = hs.add_parser("serve", help="serve every matter here (runs until stopped)")
    x.add_argument("dir")
    x.add_argument("--listen", default="0.0.0.0:7480")
    x = hs.add_parser("matters", help="the matters this hub holds")
    x.add_argument("dir")
    s = sub.add_parser("audit-api", help=argparse.SUPPRESS)
    s.add_argument("--socket", required=True)
    s.add_argument("--session", required=True)
    matter_everywhere(sub)
    a = p.parse_args(argv)

    if a.cmd == "init":
        m = Matter.create(a.path)
        out({"matter": str(m.root)})
        return
    if a.cmd == "hub":
        return _hub(a)
    if a.cmd == "sync" and a.action == "join":
        from . import invite
        out(invite.join(a.code, a.path, a.name, a.address))
        return
    if a.cmd == "setup":
        from . import setup
        try:
            out(setup.setup(a.check, not a.no_viewer, not a.no_image, a.no_cache))
        except setup.SetupError as e:
            print(json.dumps({"error": str(e)}), file=sys.stderr)
            sys.exit(1)
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
    if a.cmd == "ingest":
        if a.manifest and (a.paths or a.collection or a.custodian):
            p.error("ingest: give --manifest alone, or paths with --collection")
        if not a.manifest and not (a.paths and a.collection):
            p.error("ingest: needs paths and --collection, or --manifest")
    m = Matter.find(a.matter)
    if a.cmd == "ingest":
        from .ingest import ingest_manifest, ingest_paths

        def report(doc, name, ext, children):
            out({"doc": doc, "name": name, "kind": ext.kind,
                 "layers": [{"view": l.view, "chars": len(l.text)} for l in ext.layers],
                 "children": children})
        if a.manifest:
            ingest_manifest(m, a.manifest, report)
        else:
            ingest_paths(m, a.paths, a.custodian, a.collection, report)
    elif a.cmd == "withdraw":
        doc = locators.parse("doc:" + a.doc.removeprefix("doc:"), m.objects)["doc"]
        out(m.logs["store"].append(op="withdraw", doc=doc, reason=a.reason))
    elif a.cmd == "view":
        out({"viewer": viewmod.launch(m, a.user, a.wait), "socket": str(viewmod.socket_path(m))})
    elif a.cmd == "annotate-as-user":
        # The viewer's write path for the user's own tags and notes (host only).
        out({"events": annotations.write(m, f"user:{a.user}", json.loads(sys.stdin.read()))})
    elif a.cmd == "audit":
        from . import audit
        if not audit.audit_dir(m).exists():
            raise ValueError("no audit log here: it is kept on the host, outside the session, and starts with "
                             "the first `adhoc open`")
        if a.action == "sessions":
            for row in audit.sessions(m):
                out(row)
        elif a.action == "show":
            for ev in audit.show(m, a.session, a.kind, a.full):
                out(ev)
        else:
            report = audit.AuditLog(audit.audit_dir(m)).verify()
            out(report)
            if not report["ok"]:
                sys.exit(1)
    elif a.cmd in ("publish", "retire"):
        from . import work
        if a.cmd == "publish":
            out(work.publish(m, f"user:{a.user}", work.name_of(m, a.path), a.revises, a.merge))
        else:
            out(work.retire(m, f"user:{a.user}", a.name))
    elif a.cmd == "sync":
        _sync(m, a)
    elif a.cmd == "audit-api":
        from . import audit
        print(json.dumps({"serving": a.socket, "session": a.session}), file=sys.stderr, flush=True)
        try:
            audit.serve(m, a.socket, a.session)
        except KeyboardInterrupt:
            pass
    elif a.cmd == "ann-api":
        path = a.socket or annapi.socket_path(m)
        print(json.dumps({"serving": str(path)}), file=sys.stderr, flush=True)
        try:
            annapi.serve(m, path)
        except KeyboardInterrupt:
            pass


SYNC_HELP = """\
Sync this matter with its other installations: laptops, desktops, a hub.
Every installation keeps a full copy and works offline. Documents, tags,
notes, entities and published work sync; the workspace doesn't. See SYNC.md.

  adhoc sync invite --user me --address myhost:7480   (on a member) prints a code
  adhoc sync serve --listen 0.0.0.0:7480              (on a member) to be reached
  adhoc sync join <code> ~/matters/smith --name "Bob's desktop"   (the new one)
  adhoc sync now                                      sync with every member
  adhoc sync status | members | conflicts

An invitation is a password: send it the way you'd send one."""


def _sync(m, a):
    from . import invite, lan as lanmod, peer, pool, sync
    if a.action == "status":
        st = sync.status(m)
        st["members"] = pool.Pool.load(m).summary(st["origin"])
        out(st)
    elif a.action == "conflicts":
        for c in sync.conflicts(m):
            out(c)
    elif a.action == "members":
        for row in pool.Pool.load(m).summary(m.origin):
            out(row)
    elif a.action == "invite":
        out(invite.create(m, f"user:{a.user}", a.address, a.role, a.hours, a.name, a.same_person))
    elif a.action == "vouch":
        out(pool.vouch(m, f"user:{a.user}", a.origin))
    elif a.action == "remove":
        out({**pool.remove_member(m, f"user:{a.user}", a.origin),
             "note": "No member will sync with it once they have this removal (run adhoc sync now). It keeps "
                     "what it already has: removal can't recall copies."})
    elif a.action == "now":
        for row in peer.sync_all(m):
            out(row)
    elif a.action == "push":
        from . import hub
        out(hub.push(m, a.code, f"user:{a.user}"))
    elif a.action == "serve":
        if a.advertise:
            if not a.user:
                raise ValueError("--advertise needs --user")
            if not pool.founded(m):
                pool.found(m, f"user:{a.user}", __import__("socket").gethostname(), a.advertise)
            else:
                pool.set_addresses(m, f"user:{a.user}", a.advertise)
        host, port = peer.split_address(a.listen)
        srv = peer.Server([m], host, port)
        print(json.dumps({"serving": m.id, "origin": m.origin, "listen": f"{host}:{srv.address[1]}"}),
              file=sys.stderr, flush=True)
        lan_port = None if a.no_lan else lanmod.PORT
        if not a.no_lan:
            lanmod.Announcer(srv).start()
        if not a.no_watch:
            import threading
            from . import watch
            threading.Thread(target=watch.run, args=(m, a.every * 60, lan_port), daemon=True).start()
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            pass
    elif a.action == "watch":
        from . import watch
        sys.exit(watch.run(m, a.every * 60, None if a.no_lan else lanmod.PORT))


HUB_HELP = """\
A hub is an always-on member with a stable address: a firm server, say. It
holds every matter pushed to it, so members can sync while each other's
machines are off, and invitations made on a member are left there. It runs
no sessions.

  adhoc hub init /srv/adhoc-hub --name "Firm hub" --address hub.firm.example:7480
  adhoc hub enroll /srv/adhoc-hub --person alice     prints a code for Alice
  adhoc hub serve /srv/adhoc-hub                     (keep it running)

Alice, on her machine:  adhoc --matter ~/matters/smith sync push <code> --user alice

A hub code lets its holder put matters on the hub; it's a password.
Matters already on the hub are governed by their own pools."""


def _hub(a):
    from . import hub, peer
    if a.action == "init":
        h = hub.Hub.init(a.dir, a.name, a.address)
        out({"hub": str(h.root), "name": a.name, "addresses": a.address, "origin": h.identity.origin})
        return
    h = hub.Hub(a.dir)
    if a.action == "enroll":
        out(h.enroll(a.person))
    elif a.action == "revoke":
        out({"person": a.person, "revoked": h.revoke(a.person)})
    elif a.action == "matters":
        for m in h.matters():
            from . import sync
            st = sync.status(m)
            out({"matter": m.id, "path": str(m.root), "adopted_by": m.config.get("adopted_by"),
                 "origins": len(st["origins"])})
    elif a.action == "serve":
        host, port = peer.split_address(a.listen)
        srv = peer.Server(h.matters(), host, port, hub=h)
        print(json.dumps({"hub": h.config["name"], "listen": f"{host}:{srv.address[1]}",
                          "matters": len(srv.matters)}), file=sys.stderr, flush=True)
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            pass


# -- layers and text ---------------------------------------------------------------------

@run
def layers(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    rest = argv[2:] if argv[:1] == ["--matter"] else argv
    if rest[:1] == ["ocr"]:
        return layers_ocr(argv[:len(argv) - len(rest)] + rest[1:])
    p = parser("layers", "List the text layers of a document (or content hash).\n"
                         "layers ocr <doc> [--dpi N] [--psm N]: OCR it again, as a new layer in the workspace.")
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


def layers_ocr(argv):
    """A new OCR layer, made in the workspace (the store is read-only). It
    joins the workspace change feed, so indexes pick it up; search it with
    --layers ocr or --layers all."""
    from .extract import Extraction, unextracted_kind
    from .extract.pdf import ocr_layer
    from .ingest import store_layers
    p = parser("layers ocr", layers_ocr.__doc__)
    p.add_argument("doc")
    p.add_argument("--dpi", type=int, default=400)
    p.add_argument("--psm", type=int, default=3, help="tesseract page segmentation mode (3 auto, 4 one column, 6 one block)")
    a = p.parse_args(argv)
    m = Matter.find(a.matter)
    doc = locators.parse("doc:" + a.doc.removeprefix("doc:"), m.objects)["doc"]
    state = m.state
    content = state.documents[doc]
    meta = m.objects.get_object(state.metadata[doc])
    data = m.objects.get_bytes(content)
    kind = meta.get("kind")
    if kind not in ("pdf", "image"):
        # matters ingested before failed extractions kept their kind recorded a PDF as "other"
        kind = unextracted_kind(meta.get("name"), data)
    if kind not in ("pdf", "image"):
        raise ValueError(f"only PDFs and images can be OCRed (this is {meta.get('kind')})")
    layer = ocr_layer(data, kind, meta.get("name"), a.dpi, a.psm)
    before = {l.id for l in state.layers.get(content, [])}
    store_layers(m, content, Extraction(kind=kind, layers=[layer]), log="workspace", objects=m.workspace_objects)
    new = [l for l in m.state.layers.get(content, []) if l.id not in before]
    confs = [g["conf"] for g in layer.geometry if "conf" in g]
    out({"doc": doc, "layer": new[0].id if new else "(identical to an existing layer)", "view": "ocr",
         "chars": len(layer.text), "mean_confidence": round(sum(confs) / len(confs), 3) if confs else None,
         "settings": {"dpi": a.dpi, "psm": a.psm}})


@run
def text(argv=None):
    p = parser("text", "Print the text at a locator, after checking it.")
    p.add_argument("locator", help="string or JSON locator")
    p.add_argument("--path", action="store_true", help="print the layer text file's path instead")
    p.add_argument("--json", action="store_true", help="print the resolution (status, locator, text) as JSON")
    a = p.parse_args(argv)
    m = Matter.find(a.matter)
    if a.locator.startswith(("bates:", "exh:")):
        from .exhibits import resolve_exhibit
        from .produce import resolve_bates
        for hit in (resolve_bates if a.locator.startswith("bates:") else resolve_exhibit)(m, a.locator):
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
    s.add_argument("--since", help='cursor JSON, e.g. \'{"store/o_…": "ev_…", "workspace": "ev_…"}\': '
                   'the last event read in each log. Each event names its log ("log")')
    s = sub.add_parser("new", help="register a custom index updated by your own command")
    s.add_argument("name")
    s.add_argument("--updater", required=True, help='command that reads change events on stdin, applies them, and writes the manifest\'s "cursor": '
                   'cursor[ev["log"]] = ev["id"] for each event. Skip events marked "stale" (overtaken by later ones)')
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
             "cursor": {}, "updater": a.updater}, indent=2) + "\n", encoding="utf-8", newline="\n")
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
search all [--where "custodian:smith-j"] [--tag …]   every document (no text matched), by date

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
    if argv and argv[0] in ("grep", "similar", "all"):
        mode, argv = argv[0], argv[1:]
    p = parser("search", SEARCH_HELP)
    p.add_argument("q", metavar="query|regex|locator", nargs="?" if mode == "all" else None)
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
    if mode == "all":
        if a.q:
            p.error("search all takes no query; filter with --where, --tag or --within")
        a.q, a.by = "", "doc"
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
    if mode == "all":
        header, items = s.all()
    elif mode == "grep":
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

LIST_HELP = """\
Make and change list files (*.list.jsonl).

Key sets: the few documents that explain something, with every other
relevant document accounted for (format in adhocdocs.sets). Make one from
candidate lists (search --list, ann find --list):

  list condense pod-*.list.jsonl --out pod.set.list.jsonl --question "Were the goods delivered?"

That folds duplicates, copies of one email, emails quoted by later ones and
near-duplicates under a representative. Then record your judgment:

  list set pod.set.list.jsonl <doc> why="the signed proof of delivery"
  list cover pod.set.list.jsonl <doc>... --by <doc> --note "same facts as the carrier's notice"
  list uncover pod.set.list.jsonl <doc>      (a covered document that matters on its own)

Points say what the set must establish; then find the gaps:

  list point pod.set.list.jsonl delivered "The pallet reached Dock 4 on Feb. 24"
  list set pod.set.list.jsonl <doc> points=delivered,signed
  list points pod.set.list.jsonl    (each point's documents, and the points with none)

`list ls` finds the lists in the workspace, newest first, with their
questions, sizes and unsupported points.
"""


@run
def list_(argv=None):
    p = parser("list", LIST_HELP)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("new", help="make a list from locators on stdin, one per line")
    s.add_argument("out")
    s.add_argument("--title", required=True)
    s.add_argument("--columns", nargs="*")
    s.add_argument("--question", help="what the list answers (for a key set)")
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
    s = sub.add_parser("condense", help="a key set from candidate lists: redundant documents folded under "
                                        "a representative")
    s.add_argument("inputs", nargs="+", metavar="inp")
    s.add_argument("--out", required=True)
    s.add_argument("--title")
    s.add_argument("--question", help="what the set answers")
    s.add_argument("--near", type=float, default=0.9, help="near-duplicate similarity (0 turns it off)")
    s.add_argument("--quoted", type=float, default=0.9,
                   help="how much of an email a later one must contain to cover it (0 turns it off)")
    s = sub.add_parser("set", help="set fields on an item, e.g. why=\"the signed POD\" (key= removes one)")
    s.add_argument("file")
    s.add_argument("doc")
    s.add_argument("values", nargs="+", metavar="key=value")
    s = sub.add_parser("cover", help="put documents under the item that covers them (a judgment: restates)")
    s.add_argument("file")
    s.add_argument("docs", nargs="+")
    s.add_argument("--by", required=True, help="the document that covers them")
    s.add_argument("--reason", default="restates")
    s.add_argument("--note", help="why")
    s = sub.add_parser("uncover", help="make covered documents representatives again")
    s.add_argument("file")
    s.add_argument("docs", nargs="+")
    s = sub.add_parser("point", help="add a point a key set must establish, or change its text")
    s.add_argument("file")
    s.add_argument("id", help="lowercase letters, digits and hyphens")
    s.add_argument("text", nargs="?")
    s.add_argument("--remove", action="store_true", help="remove the point, and its ID from every item")
    s = sub.add_parser("points", help="each point's supporting documents, and the points nothing supports")
    s.add_argument("file")
    s = sub.add_parser("ls", help="the list files in the workspace, newest first")
    s.add_argument("dir", nargs="?", help="default: the matter's workspace")
    argv = sys.argv[1:] if argv is None else argv
    argv = [" " + x if x.startswith("-") and not x.startswith("--") and x != "-h" else x for x in argv]
    matter_everywhere(sub)
    a = p.parse_args(argv)
    a.tag = [t.strip() for t in getattr(a, "tag", None) or []]

    if a.cmd == "new":
        header, items = lists.new(a.title, [line.strip() for line in sys.stdin if line.strip()], a.columns)
        if a.question:
            header["question"] = a.question
        lists.write(a.out, header, items)
        out({"written": a.out, "items": len(items)})
        return
    if a.cmd == "cat":
        for f in a.files:
            for it in lists.read(f)[1]:
                out(it)
        return
    if a.cmd in ("point", "points"):
        from . import sets
        header, items = lists.read(a.file)
        if a.cmd == "points":
            out(sets.points_report(header, items))
            return
        if a.remove:
            sets.remove_point(header, items, a.id)
        elif not a.text:
            raise ValueError("give the point's text, or --remove")
        else:
            sets.set_point(header, a.id, a.text)
        lists.write(a.file, header, items)
        out({"written": a.file, "points": [p["id"] for p in header.get("points", [])]})
        return
    m = Matter.find(a.matter)
    if a.cmd == "ls":
        for row in lists.catalog(a.dir or m.workspace):
            out(row)
        return
    if a.cmd in ("condense", "set", "cover", "uncover"):
        from . import sets

        def doc(s):
            return locators.parse("doc:" + s.removeprefix("doc:"), m.objects)["doc"]
        if a.cmd == "condense":
            header, items, summary = sets.condense(m, [(f, *lists.read(f)) for f in a.inputs], a.title,
                                                   a.question, a.near, a.quoted)
            lists.write(a.out, header, items)
            out({"written": a.out, **summary})
            return
        header, items = lists.read(a.file)
        if a.cmd == "set":
            values = {}
            for kv in a.values:
                k, eq, v = kv.partition("=")
                if not eq or not k:
                    raise ValueError(f"expected key=value, got {kv!r}")
                values[k] = v or None
            sets.set_fields(items, doc(a.doc), values, header)
        elif a.cmd == "cover":
            sets.cover(items, [doc(d) for d in a.docs], doc(a.by), a.reason, a.note)
        else:
            sets.uncover(items, [doc(d) for d in a.docs])
        lists.write(a.file, header, items)
        out({"written": a.file, "items": len(items), "covered": sum(len(it.get("children", [])) for it in items)})
        return
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

Privilege calls are a lawyer's: Claude can't apply ``privileged`` or
``redact…`` (the tags production withholds and redacts by), even when asked.
Flag a document or passage for review instead, with the reason:

  ann flag-privilege <locator>... --basis "Legal advice from GC to the CFO"

This tags it ``check-privilege`` and notes the basis on it. Production lists
flags in its "check for possible privilege" report (privilege-check.csv).
Call it that, never a privilege log: the lawyer decides.

Tags must be defined (define-tag) before use. A tag in an exclusive --group
replaces the group's other tag on the same target.

Entities (the entity layer; ENTITIES.md): people, organizations, defined
terms, contracts, events, numbers... that the user can hover over in the
viewer to be reminded what they are.

  ann entities [--kind K] [--doc D] [--suggested]   the entity list, or a document's entity layer
  ann entity <id|name> [--mentions]                 one entity (and where it is mentioned)
  ann suggestions [--doc D] [--kind K]              detected candidates nobody has defined yet
  ann define-entity "Raymond Voss" --kind person --alias Voss --alias "R. Voss" \
      --summary "Harrowgate's controller; signed the supply agreement for Seller." --see <locator>
  ann edit-entity <id|name> --recap "You asked who chased the invoice: Voss, Mar 9 and Mar 17."
  ann define-entity "Dock 4 rejection" --kind event --date 2026-03-09 --alias "the March 9 rejection" \
      --summary "Ames refused the pallet at Dock 4; the start of the dispute." --see <locator>
  ann delete-entity <id|name>

The viewer underlines known entities and dots suggestions (found by rules
when documents were indexed); the user hovers one to see what it is and
clicks it to select it, so `view selection` has its "entity". When you have
told the user who or what something is, record it: define-entity if it isn't
known (with the aliases the documents use, so every mention is marked),
else edit-entity --recap. The summary says what it is in a line or two; the
recap what you just told them, so the next hover reminds them. Both are
paraphrase: point at the words with --see, don't type quotations. Say in a
line that you did it. After an analysis that names the key players (a cast,
a chronology), add the ten or so that matter, not every suggestion.

Events with a --date go on the viewer's date cards (DATES.md): hovering a
date in a document shows the nearest events before and after it. Add the
events that turn the story (a dozen or so, not every document) when you
write a chronology, or when the user asks what matters when.

Claude can't change a field, remove an alias or delete an entity the user
set, made or added; it can fill in what the user left blank.
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
    s = sub.add_parser("log", help="the event log, all installations' logs merged")
    s.add_argument("--since", help="a cursor from --cursor (JSON), or an event ID. Events synced from other "
                   "installations can sort before ones you've read; a cursor still catches them, an ID doesn't")
    s.add_argument("--cursor", action="store_true", help="print only the cursor for the log's end")
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
    s = sub.add_parser("flag-privilege", help="flag documents or passages: check for possible privilege, with --basis")
    s.add_argument("locators", nargs="+")
    s.add_argument("--basis", required=True, help="why it may be privileged (Markdown; quotations as references)")
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
    s = sub.add_parser("entities", help="the entity list, or with --doc a document's entity layer")
    s.add_argument("--kind")
    s.add_argument("--doc", help="a document: the known entities' mentions in it (and --suggested ones)")
    s.add_argument("--suggested", action="store_true", help="with --doc: also detected candidates")
    s = sub.add_parser("entity", help="one entity by ID, name or alias")
    s.add_argument("ref")
    s.add_argument("--mentions", action="store_true", help="every mention, as locators")
    s = sub.add_parser("suggestions", help="detected entity candidates not in the entity list, most documents first")
    s.add_argument("--doc")
    s.add_argument("--kind")
    s.add_argument("--min-docs", type=int, default=1)
    s.add_argument("--limit", type=int, default=50)
    for name in ("define-entity", "edit-entity"):
        s = sub.add_parser(name)
        s.add_argument("name" if name == "define-entity" else "ref")
        if name == "edit-entity":
            s.add_argument("--name")
        s.add_argument("--kind", help="person, organization, term, identifier, contract, event, place, thing, other")
        s.add_argument("--summary", help="what it is, in a line or two: shown when the user hovers over it")
        s.add_argument("--recap", help="what you last told the user about it, shown under the summary")
        s.add_argument("--see", action="append", help="a locator to the words that define or show it (repeatable)")
        s.add_argument("--alias", dest="aliases", action="append", help="another way it is written (repeatable)")
        s.add_argument("--date", help="an event's date (YYYY-MM-DD, YYYY-MM or YYYY): it goes on the viewer's date cards")
        if name == "edit-entity":
            s.add_argument("--remove-alias", action="append")
            s.add_argument("--clear-see", action="store_true", help="remove every --see")
        s.add_argument("--requested-by")
    s = sub.add_parser("delete-entity")
    s.add_argument("ref")
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
        if a.cursor:
            out(m.annotation_log.cursor())
            return
        since = json.loads(a.since) if a.since and a.since.lstrip().startswith("{") else a.since
        for ev in m.annotation_log.read(since):
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
    elif a.cmd in ("entities", "entity", "suggestions"):
        _entity_reads(m, a)
    elif a.cmd in ("define-entity", "edit-entity", "delete-entity"):
        req = {"op": a.cmd, "requested_by": a.requested_by}
        if a.cmd != "define-entity":
            req["entity"] = entities.resolve(m, a.ref)["entity"]
        if a.cmd == "define-entity":
            req.update(name=a.name, aliases=a.aliases)
        elif a.cmd == "edit-entity":
            req.update(name=a.name, add_aliases=a.aliases, remove_aliases=a.remove_alias)
            if a.clear_see:
                req["see"] = []
        if a.cmd != "delete-entity":
            req.update(kind=a.kind, summary=a.summary, recap=a.recap, date=a.date)
            if a.see:
                req["see"] = _targets(m, a.see)
        out(annapi.request(m, {k: v for k, v in req.items() if v is not None}))
    elif a.cmd == "flag-privilege":
        req = {"requested_by": a.requested_by}
        events = []
        if annotations.PRIVILEGE_CHECK not in annotations.Annotations.load(m).definitions:
            events += annapi.request(m, {**req, "op": "define-tag", "tag": annotations.PRIVILEGE_CHECK,
                                         "description": annotations.PRIVILEGE_CHECK_DESCRIPTION})["events"]
        for t in _targets(m, a.locators, a.family, a.dupes):
            events += annapi.request(m, {**req, "op": "tag", "tag": annotations.PRIVILEGE_CHECK, "target": t})["events"]
            events += annapi.request(m, {**req, "op": "note", "target": t, "body": a.basis})["events"]
        out({"events": events})
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


def _entity_row(e: dict) -> dict:
    row = {k: e[k] for k in ("entity", "name", "kind", "aliases", "summary", "recap", "see", "date", "by", "at") if e.get(k) not in (None, [])}
    users = sorted({b for b in [*e["set_by"].values(), *e["alias_by"].values()] if annotations.is_user(b)})
    if users:
        row["user_set"] = sorted(f for f, b in e["set_by"].items() if annotations.is_user(b))
    if e.get("edited_at"):
        row["edited_at"] = e["edited_at"]
    if e.get("set_at", {}).get("recap") and e.get("recap"):
        row["recap_at"] = e["set_at"]["recap"]
    return row


def _entity_reads(m, a):
    ents = entities.known(m)
    if a.cmd == "entity":
        e = entities.resolve(m, a.ref, ents)
        row = _entity_row(e)
        ms = entities.mentions(m, [e])
        row["mentioned_in"] = len({x["loc"]["doc"] for x in ms})
        row["mentions"] = len(ms)
        out(row)
        if a.mentions:
            for x in ms:
                out({"loc": x["loc"]})
    elif a.cmd == "entities" and not a.doc:
        for e in ents:
            if not a.kind or e["kind"] == a.kind:
                out(_entity_row(e))
    elif a.cmd == "entities":
        lay = entities.layer(m, _targets(m, [a.doc])[0]["doc"], suggestions=a.suggested)
        by_id = {e["entity"]: e for e in ents}
        for x in lay["known"]:
            if not a.kind or any(by_id[i]["kind"] == a.kind for i in x["entities"]):
                out({"loc": x["loc"], "entities": [{"entity": i, "name": by_id[i]["name"]} for i in x["entities"]]})
        for x in lay.get("suggested", []):
            if not a.kind or x["kind"] == a.kind:
                out({"loc": x.pop("loc"), "suggested": {k: v for k, v in x.items() if k not in ("start", "end")}})
    else:
        have = entities.known_keys(ents)
        cands = entities.candidates(m)
        if not cands:
            raise ValueError("the entities index has no candidates yet; idx status entities")
        keys = list(cands)
        if a.doc:
            doc = _targets(m, [a.doc])[0]["doc"]
            lay = m.state.primary_layer(m.state.documents[m.state.follow(doc)])
            keys = list(dict.fromkeys(d["key"] for d in entities.detected(m, lay.text))) if lay else []
        rows = [{"key": k, **cands[k]} for k in keys if k in cands and k not in have
                and (not a.kind or cands[k]["kind"] == a.kind) and cands[k]["docs"] >= a.min_docs]
        rows.sort(key=lambda r: (-r["docs"], -r["mentions"]))
        for r in rows[:a.limit]:
            out(r)


# -- page ----------------------------------------------------------------------------------

PAGE_HELP = """\
Literate pages: Markdown whose quotations are references to document text.

  page ref add page.md <id> <locator>...   add a reference (several locators: an elided quotation)
  page ref add page.md <id> --in <doc> --words "<the document's words>" [--words …]
                                           the same, found from the words to quote
  page check page.md                       resolve every reference and lint
  page refs update page.md                 rewrite re-anchored references
  page render page.md [-o out.html]        static HTML (the viewer fills in quotations)
  page view page.md [--pane P]             render it into a viewer pane
  page export page.md --format html|docx|pdf -o out

References can be text ranges (from search hits or `view selection`),
spreadsheet cells (`sheet=…&cells=B2:D10`, e.g. a grid selection) or page
regions (`page=3&xywh=…`, e.g. an Alt-drag selection).

Format:
""" + __import__("adhocdocs.literate", fromlist=["x"]).__doc__.split("\n", 1)[1]


@run
def page(argv=None):
    from . import literate, pages
    argv = list(sys.argv[1:] if argv is None else argv)
    i = 2 if argv[:1] == ["--matter"] else 0              # `page --matter M ref add …` too
    if argv[i:i + 2] == ["ref", "add"]:
        argv[i:i + 2] = ["ref-add"]
    elif argv[i:i + 2] == ["refs", "update"]:
        argv[i:i + 2] = ["refs-update"]
    p = parser("page", PAGE_HELP)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("check")
    s.add_argument("file")
    s.add_argument("--all", action="store_true", help="also print references that are fine")
    s = sub.add_parser("render")
    s.add_argument("file")
    s.add_argument("-o", "--out", help="default: stdout")
    s = sub.add_parser("view", help="render the page into a viewer pane (pictures of pages included)")
    s.add_argument("file")
    s.add_argument("--pane", help="replace this page pane's content")
    s.add_argument("--window", help="default: the window the user last used")
    s = sub.add_parser("export")
    s.add_argument("file")
    s.add_argument("--format", choices=["html", "docx", "pdf"], required=True)
    s.add_argument("-o", "--out", required=True)
    s.add_argument("--allow-reanchored", action="store_true")
    s = sub.add_parser("ref-add", help="page ref add <file> <id> <locator>... | --in DOC --words WORDS...",
                       description="Add a reference from locators, or from the words to quote: "
                       "--words is matched exactly in the document's text, except that whitespace "
                       "(line breaks too) and straight/curly quote marks may differ, and must occur "
                       "once. Repeat --words for an elided quotation (in order).")
    s.add_argument("file")
    s.add_argument("id")
    s.add_argument("locators", nargs="*", help="string or JSON locators (e.g. a search hit's loc)")
    s.add_argument("--in", dest="in_doc", metavar="DOC",
                   help="with --words: a doc locator or hash prefix, or part of its file name; "
                        "default: whichever one document has the words")
    s.add_argument("--words", action="append", help="the words to quote, as they appear in the document")
    s.add_argument("--layer", metavar="VIEW", help="with --words: search this layer view (e.g. ocr), not the primary")
    s.add_argument("--replace", action="store_true")
    s = sub.add_parser("refs-update", help="page refs update <file>")
    s.add_argument("file")
    s = sub.add_parser("docs", help="the documents a page cites, in order (--list to write them as a list file)")
    s.add_argument("file")
    s.add_argument("--list", dest="list_file")
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
                Path(a.out).write_text(html_doc, encoding="utf-8", newline="\n")
                out({"written": a.out, "problems": len([r for r in results if "lint" in r or r.get("status") != "exact"])})
            else:
                sys.stdout.write(html_doc)
        elif a.cmd == "view":
            import base64
            page = pages.Page(m, a.file)
            html_doc, results = pages.render(page, standalone=True, images="files")
            files = {"index.html": base64.b64encode(html_doc.encode()).decode(),
                     **{name: base64.b64encode(png).decode() for name, png in page.images.items()}}
            c = {"cmd": "pane", "kind": "page", "files": files, "title": page.meta.get("title") or Path(a.file).stem,
                 "update": a.pane, "window": a.window}
            shown = viewmod.command(m, {k: v for k, v in c.items() if v is not None})
            out({**shown, "problems": len(literate.problems(results))})
        elif a.cmd == "export":
            out(pages.export(pages.Page(m, a.file), a.format, Path(a.out), a.allow_reanchored))
        elif a.cmd == "ref-add":
            if bool(a.locators) == bool(a.words):
                raise pages.PageError("give locators, or --words (with --in), not both")
            locs = a.locators or pages.find_words(m, a.in_doc, a.words, a.layer)
            out(pages.add_ref(m, a.file, a.id, locs, a.replace))
        elif a.cmd == "refs-update":
            for c in pages.update_refs(m, a.file):
                out(c)
        elif a.cmd == "docs":
            page = pages.Page(m, a.file)
            cited = pages.cited_docs(page)
            items = [{"loc": "doc:" + d, "fields": {"refs": ", ".join(ids)}} for d, ids in cited]
            if a.list_file:
                header = {"type": "list", "v": 1, "title": page.meta.get("title") or Path(a.file).stem,
                          "columns": ["name", "date", "custodian", "refs", "tags"], "made_by": "page",
                          "page": os.path.relpath(a.file, Path(a.list_file).resolve().parent)}
                lists.write(a.list_file, header, items)
                out({"written": a.list_file, "items": len(items)})
            else:
                for it in items:
                    out(it)
    except pages.PageError as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)


# -- view ------------------------------------------------------------------------------------

VIEW_HELP = """\
Drive the document viewer. The viewer runs on the host (the user starts it);
this only talks to it. Locators are string or JSON; hash prefixes work.

Keys in the viewer: j/k move, Enter open, ]/[ next/previous document,
Space page, m mark, t tag palette (Shift+Enter tags the family, Ctrl+Enter
the duplicates), N note, e entity (add the selection, or edit the entity it
is), E show entities (all, known, none), tag hotkeys, v layer, f mode, Tab pane,
H overview, Alt-drag a region on a page, ? help. Spreadsheets open as a grid: click, drag, or
Shift+arrows select cells, and row and column headers select whole rows
and columns. The selection is a `sheet=…&cells=B2:D10` locator.

Entities: the viewer underlines known entities and dots suggested ones;
the user hovers to see what they are and clicks to select one. `view
selection` then has "entity": {"known": [...]} or {"suggested": {...}}.
See `ann --help` for the entity list.

Timelines: `view timeline FILE` shows a list file's items by date, in
lanes (--lanes group, custodian, from, kind). For a chronology, give items
their own fields "date" (YYYY-MM-DD, YYYY-MM or YYYY) and "event" (the
label), each with a locator to its evidence.

Layout: a new pane (`view pane --new`, `view list`, `view open --new-pane`)
gets a tab of its own. What the user opens from a pane (a page's
references, a list's preview) goes beside it, tabbed there, so only related
views share the width. `view state` shows the arrangement (windows[].tree).

Windows: the viewer can have several, each with its own panes (say, a list
on one screen and documents on another). Pane IDs are unique across them
(p1 in the first window, w2-p1 in the second), a command naming a pane goes
to that pane's window, and others go to the window the user last used, or
to --window. A list pane can be linked to a document pane in another
window. The user moves a pane to a new window with W; `view window new
--pane P` does the same.

Services: a pane's <form action="svc:NAME"> is answered immediately by
`view serve NAME -- cmd …` (run it in the background), without waiting for
the next prompt. cmd gets {"form", "selection", "pane"} as JSON on stdin and
prints the pane's new HTML.
"""


def where(s):
    """Which window a new pane goes in (a command naming a pane goes to its window)."""
    g = s.add_mutually_exclusive_group()
    g.add_argument("--window", help="default: the window the user last used")
    g.add_argument("--new-window", action="store_true", help="in a new window")


@run
def view(argv=None):
    p = parser("view", VIEW_HELP)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("open", help="open a locator, scrolled to and briefly highlighted")
    s.add_argument("loc")
    s.add_argument("--pane")
    s.add_argument("--new-pane", action="store_true")
    s.add_argument("--layer", dest="view_", help="layer view: native, ocr, transcript, …")
    s.add_argument("--mode", choices=["page", "flow", "text", "grid"], help="grid: spreadsheets as a table of cells")
    where(s)
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
    where(s)
    s = sub.add_parser("pane", help="show static HTML (a file or a directory bundle with index.html)")
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--new", action="store_true")
    g.add_argument("--update", metavar="PANE")
    s.add_argument("path")
    s.add_argument("--title")
    s.add_argument("--link", metavar="DOC_PANE", help="a document pane that follows the selection")
    s.add_argument("--preview-new", action="store_true", help="make a new linked document pane")
    where(s)
    s = sub.add_parser("list", help="show a list file, with a linked preview pane")
    s.add_argument("file")
    s.add_argument("--pane", help="replace this list pane's content")
    s.add_argument("--preview", metavar="DOC_PANE", help="a document pane to follow the selection, in any window")
    where(s)
    s = sub.add_parser("timeline", help="show a list file's items by date, in lanes, with a linked preview pane")
    s.add_argument("file", help="a list file; items' fields date and event make it a chronology")
    s.add_argument("--lanes", choices=["auto", "group", "custodian", "from", "kind", "none"], default="auto",
                   help="auto: the list's groups if it has them, else custodian")
    s.add_argument("--scale", choices=["auto", "day", "week", "month", "year"], default="auto")
    s.add_argument("--title")
    s.add_argument("--pane", help="replace this timeline pane's content")
    s.add_argument("--preview", metavar="DOC_PANE", help="a document pane to follow the selection, in any window")
    where(s)
    s = sub.add_parser("close")
    s.add_argument("pane")
    s = sub.add_parser("home", help="show the overview: what the matter holds (the first window opens on it)")
    s.add_argument("--window", help="default: the window the user last used")
    s = sub.add_parser("layout", help="a window's tabs one at a time (tabs, the default) or tiled")
    s.add_argument("layout", choices=["tabs", "single", "split-v", "split-h", "grid"], help="single: the same as tabs")
    s.add_argument("--window", help="default: the window the user last used")
    s = sub.add_parser("window", help="windows: new (optionally moving a pane into it), move a pane, close")
    s.add_argument("action", choices=["new", "move", "close"])
    s.add_argument("--pane", help="new, move: the pane to move")
    s.add_argument("--window", help="move: the window to move it to; close: the window")
    s.add_argument("--layout", choices=["tabs", "single", "split-v", "split-h", "grid"], help="new: its layout")
    sub.add_parser("state", help="windows, panes, what's visible, selections, marks, form values")
    sub.add_parser("selection", help="the focused pane's selection and marks, and the entity or date it is "
                                     "(a date with its resolved day: analyze around DAY says what is near it)")
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
        if x.startswith("exh:"):
            from .exhibits import resolve_exhibit
            return locators.format(resolve_exhibit(m, x)[0]["loc"])
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
    elif a.cmd == "timeline":
        from .timeline import timeline_html
        files, sequence, summary = timeline_html(m, Path(a.file), a.lanes, a.scale, a.title)
        header, _ = lists.read(a.file)
        c = {"cmd": "pane", "kind": "timeline", "files": files, "sequence": sequence,
             "title": a.title or header.get("title") or Path(a.file).stem, "update": a.pane,
             "link": a.preview, "preview_new": not a.preview and not a.pane}
    elif a.cmd in ("close",):
        c["pane"] = a.pane
    elif a.cmd in ("layout", "home"):
        c.update(layout=getattr(a, "layout", None), window=a.window)
    elif a.cmd == "window":
        if a.action in ("move",) and not (a.pane and a.window):
            raise ValueError("view window move needs --pane and --window")
        if a.action == "close" and not a.window:
            raise ValueError("view window close needs --window")
        c = {"cmd": "window-" + a.action, "pane": a.pane, "window": a.window, "layout": a.layout}
    elif a.cmd == "history":
        c["limit"] = a.limit
    elif a.cmd in ("clear-highlights", "clear-marks"):
        c["pane"] = a.pane
    if a.cmd in ("open", "sequence", "pane", "list", "timeline"):
        c.update(window=a.window, new_window=a.new_window)
    result = viewmod.command(m, {k: v for k, v in c.items() if v is not None and v is not False})
    if a.cmd == "timeline":
        result["timeline"] = {k: summary[k] for k in ("items", "dated", "undated", "lanes", "segments", "truncated")}
    out(result)


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
    s = sub.add_parser("people", help="a cast of characters from email headers and names in document text")
    s.add_argument("--mentions", action="store_true", help="also every mention of each person, as locators")
    s.add_argument("--person", help="only people whose name or a way they're written contains this")
    s.add_argument("--list", dest="list_file", help="with --person: a list of the documents that name them, "
                                                    "each mention as a child")
    s = sub.add_parser("redline", help="word-level changes from document A to document B")
    s.add_argument("a")
    s.add_argument("b")
    s.add_argument("--html", help="also write static HTML for a viewer pane (view pane --new FILE)")
    s = sub.add_parser("dates", help="a document's own date (and where it came from) and the dates in its text, resolved")
    s.add_argument("doc")
    s = sub.add_parser("around", help="what is near a date: documents, dated events, chronology entries")
    s.add_argument("date", help="YYYY-MM-DD")
    s.add_argument("--days", type=int, default=7, help="how far either side (default 7)")
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
        if a.list_file and not a.person:
            raise ValueError("--list needs --person")
        from .names import key
        found = analysis.people(m, a.mentions or bool(a.list_file))
        if a.person:
            want = key(a.person)
            found = [p_ for p_ in found if any(want in key(n) for n in [p_["name"], *p_["names"], *p_["written_as"]])]
        if a.list_file:
            from .index import open_index
            rows = open_index(m, "fields").rows()
            by_doc = {}
            for p_ in found:
                for x in p_["mentions"]:
                    by_doc.setdefault(x["loc"]["doc"], []).append({"loc": x["loc"], "fields": {
                        "person": p_["name"], "basis": ", ".join(x["basis"]), **({"role": x["role"]} if x.get("role") else {})}})
            docs = sorted(by_doc, key=lambda d: ((rows.get(d) or {}).get("date") or "9999", d))
            items = [{"loc": "doc:" + d, "children": by_doc[d]} for d in docs]
            lists.write(a.list_file, {"type": "list", "v": 1, "title": "Mentions of " + ", ".join(p_["name"] for p_ in found),
                                      "columns": ["date", "name", "custodian", "person", "role", "tags"],
                                      "made_by": "analyze people", "query": a.person}, items)
            out({"written": a.list_file, "people": [p_["name"] for p_ in found], "documents": len(items),
                 "mentions": sum(len(v) for v in by_doc.values())})
        else:
            for p_ in found:
                if not a.mentions:
                    p_.pop("mentions", None)
                out(p_)
    elif a.cmd == "dates":
        from . import dates
        out(dates.doc_mentions(m, doc(a.doc)))
    elif a.cmd == "around":
        from . import dates
        out(dates.around(m, a.date, a.days))
    elif a.cmd == "redline":
        header, changes = analysis.redline(m, doc(a.a), doc(a.b))
        out(header)
        for c in changes:
            out(c)
        if a.html:
            Path(a.html).write_text(analysis.redline_html(m, doc(a.a), doc(a.b)), encoding="utf-8", newline="\n")


# -- produce ---------------------------------------------------------------------------------

@run
def produce(argv=None):
    from . import produce as prod
    argv = list(sys.argv[1:] if argv is None else argv)
    i = 2 if argv[:1] == ["--matter"] else 0
    if argv[i:i + 1] == ["exhibits"]:
        return produce_exhibits(argv[:i] + argv[i + 1:])
    p = parser("produce", prod.__doc__ + "\n`produce exhibits --help`: documents stamped as numbered exhibits, and an exhibit list.")
    p.add_argument("list_file", help="the documents to produce, in order")
    p.add_argument("--name", required=True, help="e.g. vol001")
    p.add_argument("--prefix", required=True, help="Bates prefix, e.g. HFS")
    p.add_argument("--start", type=int, help="first number (default: the next not reserved in this matter, "
                                             "on any installation)")
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


def produce_exhibits(argv):
    from . import exhibits as ex
    from .produce import ProductionError
    p = parser("produce exhibits", ex.__doc__)
    p.add_argument("list_file", help="the exhibits, in order: one per item")
    p.add_argument("--name", required=True, help="the set, e.g. kettering-dep")
    p.add_argument("--prefix", default="Exhibit ", help='label before the number: "Exhibit " (default), "DX-", "PX "')
    p.add_argument("--start", type=int, help="first number (default: the next not reserved in this matter, "
                                             "on any installation)")
    p.add_argument("--witness", help="on the sticker, e.g. Kettering")
    p.add_argument("--date", help="on the sticker, e.g. 2026-04-28")
    p.add_argument("--originals", action="store_true", help="use originals even for documents that were produced")
    a = p.parse_args(argv)
    m = Matter.find(a.matter)
    try:
        out(ex.exhibits(m, Path(a.list_file), a.name, a.prefix, a.start, a.witness, a.date, a.originals))
    except ProductionError as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)


# -- work --------------------------------------------------------------------------------------

WORK_HELP = """\
Published work product, shared with this matter's other installations.

The workspace is yours alone. To share a page, chronology, key set, list or
production with the matter's other installations, publish it: a snapshot
goes into the store and appears in work/ (read-only here) on every
installation. Publish when a draft is ready to share, or when the user asks.

  work ls                          what's published, and any forks
  work publish <path> [--merge]    publish a file or directory in the workspace
  work checkout <name>             copy a published version into the workspace
  work log <name>                  a name's versions
  work retire <name>               take it out of work/ (the history stays)

To revise someone else's page: work checkout memo.md, edit it in the
workspace, then work publish memo.md. If two installations revised the same
version, it forks: work ls shows both, the other is in work/.forks/; check
both out, merge them, and publish with --merge."""


@run
def work(argv=None):
    from . import work as w
    p = parser("work", WORK_HELP)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ls", help="published work, with forks")
    s = sub.add_parser("publish", help="publish a workspace file or directory (through the annotation API)")
    s.add_argument("path")
    s.add_argument("--merge", action="store_true", help="revise every current version: resolves a fork")
    s.add_argument("--revises", action="append", help="the version this revises (default: the one checked out)")
    s.add_argument("--requested-by", help="user, when the user asked for this")
    s = sub.add_parser("checkout", help="copy a published version into the workspace")
    s.add_argument("name")
    s.add_argument("--version", help="default: the current one")
    s.add_argument("--force", action="store_true", help="replace workspace changes that aren't published")
    s = sub.add_parser("log", help="a name's versions")
    s.add_argument("name")
    s = sub.add_parser("retire", help="take a name out of work/ (through the annotation API)")
    s.add_argument("name")
    s.add_argument("--requested-by")
    matter_everywhere(sub)
    a = p.parse_args(argv)
    m = Matter.find(a.matter)
    if a.cmd == "ls":
        for row in w.listing(m):
            out(row)
    elif a.cmd == "log":
        for row in w.history(m, a.name):
            out(row)
    elif a.cmd == "checkout":
        out(w.checkout(m, a.name, a.version, a.force))
    elif a.cmd == "publish":
        req = {"name": w.name_of(m, a.path), "merge": a.merge, "revises": a.revises, "requested_by": a.requested_by}
        out(annapi.request(m, {k: v for k, v in req.items() if v}, "/publish"))
    elif a.cmd == "retire":
        out(annapi.request(m, {"name": a.name, **({"requested_by": a.requested_by} if a.requested_by else {})}, "/retire"))


def main():
    """``python -m adhocdocs.cli <command> …``"""
    cmds = {"adhoc": adhoc, "idx": idx, "search": search, "layers": layers, "text": text, "list": list_, "ann": ann, "page": page, "view": view, "analyze": analyze, "produce": produce, "work": work}
    if len(sys.argv) < 2 or sys.argv[1] not in cmds:
        print(f"usage: python -m adhocdocs.cli {{{','.join(cmds)}}} …", file=sys.stderr)
        sys.exit(2)
    cmds[sys.argv[1]](sys.argv[2:])


if __name__ == "__main__":
    main()
