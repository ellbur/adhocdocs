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

from . import annapi, annotations, entities, fetch as fetchmod, gui, lists, locators, view as viewmod
from .matter import Matter, MatterNotFound


def out(obj):
    print(json.dumps(obj, ensure_ascii=False))


def parser(prog, doc):
    p = argparse.ArgumentParser(prog=prog, description=doc, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--matter", help="matter directory (default: $ADHOC_MATTER or the enclosing matter)")
    return p


def _grain_arg(simple):
    """--grain: one of ``simple``, or grains joined by + (thread+family: a thread with its attachments)."""
    def check(v):
        from . import metadocs
        if v in simple:
            return v
        if metadocs.composite(v):
            return metadocs.canonical_grain(v)
        raise argparse.ArgumentTypeError(f"{v!r}: one of {', '.join(simple)}, or several of "
                                         f"{', '.join(metadocs.COMPOSABLE)} joined by + (thread+family)")
    return check


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
                ValueError, KeyError, fetchmod.Unavailable) as e:
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

def _hold():
    try:
        input("\nPress Enter to close.")
    except (EOFError, KeyboardInterrupt):
        pass


def _open(a, container) -> int:
    """adhoc open: in this terminal, the viewer's, or a terminal program's (adhocdocs.gui)."""
    m = Matter.find(a.path or a.matter)
    gui.remember(m.root, opened=True)
    terminal = a.terminal
    if terminal == "auto":
        terminal = "here" if sys.stdin.isatty() and sys.stdout.isatty() else "beside"
    if terminal == "app":
        from .portable import spawn
        argv = [*gui.adhoc_command(), "open", str(m.root), "--terminal", "here", "--hold", "--user", a.user,
                *(["--no-viewer"] if a.no_viewer else []), *(["--", *a.command] if a.command else [])]
        proc = spawn(gui.in_terminal_app(argv))
        out({"terminal": gui.terminal_app()[0], "pid": proc.pid})
        return 0
    if a.hold and sys.stdout.isatty():
        print(f"\033]0;adhocdocs: {m.root.name}: Claude Code\007", end="", flush=True)      # the terminal window's title
    code = container.open_session(m, a.user, not a.no_viewer, a.command, terminal)
    if code and a.hold:
        print(f"\nThe session ended with exit status {code}.", file=sys.stderr)
        _hold()
    return code


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
    s = sub.add_parser("ingest-production", help="add a production another party made: load files, images, natives, text",
                       description="Ingest a production volume another party served: one document per .dat record "
                                   "(Concordance or CSV), or per document of an EDRM XML load file, with page images "
                                   "from an Opticon .opt or IPRO .lfp; "
                                   "named by its Bates numbers (bates:) and control number (ctl:). Run with --check "
                                   "first: it reports the columns found and every problem, and ingests nothing. "
                                   "See docs/RECEIVED.md.")
    s.add_argument("volume", help="the volume's folder")
    s.add_argument("--party", help="who produced it, e.g. \"Acme Corp.\" (required, except with --check)")
    s.add_argument("--name", help="the volume's name (default: the folder's)")
    s.add_argument("--dat", help="the .dat load file, or an EDRM XML one (default: found in the volume)")
    s.add_argument("--opt", help="the .opt or IPRO .lfp image load file (default: found in the volume)")
    s.add_argument("--map", action="append", default=[], metavar="FIELD=COLUMN",
                   help="which .dat column a field is, when it isn't recognized (e.g. begin=\"Prod Beg\")")
    s.add_argument("--day-first", action="store_true", help="dates like 03/04/2021 are day first (4 March)")
    s.add_argument("--replaces", action="append", default=[], metavar="VOLUME",
                   help="this volume replaces a received one (say, after a clawback): its documents supersede "
                        "the ones there that begin at the same Bates numbers, and their tags and notes are "
                        "carried over to them, marked so")
    s.add_argument("--user", default=os.environ.get("USER", "user"),
                   help="with --replaces: who carries the old documents' tags and notes over (default: $USER)")
    s.add_argument("--check", action="store_true", help="read the volume and report; ingest nothing")
    s = sub.add_parser("overlay-production", help="apply an overlay: a later .dat correcting received documents' fields",
                       description="Apply an overlay load file to documents already received: each record is "
                                   "matched by its begin Bates number (else its control number), and its non-blank "
                                   "fields replace or add to the document's. Files, families and custodians aren't "
                                   "changed. Run with --check first. See docs/RECEIVED.md.")
    s.add_argument("dat", help="the overlay load file (.dat, .csv or tab-separated)")
    s.add_argument("--volume", help="the received volume it is for (needed only when two volumes have the same numbers)")
    s.add_argument("--map", action="append", default=[], metavar="FIELD=COLUMN",
                   help="which column a field is, when it isn't recognized")
    s.add_argument("--day-first", action="store_true", help="dates like 03/04/2021 are day first (4 March)")
    s.add_argument("--check", action="store_true", help="report what would change; change nothing")
    s = sub.add_parser("withdraw", help="remove a document from the matter (e.g. clawback)")
    s.add_argument("doc")
    s.add_argument("--reason", required=True)
    s = sub.add_parser("open", help="start a session for a matter: the container, the viewer and the annotation API")
    s.add_argument("path", nargs="?", help="the matter (default: --matter, $ADHOC_MATTER or the enclosing matter)")
    s.add_argument("--no-viewer", action="store_true")
    s.add_argument("--user", default=os.environ.get("USER", "user"), help="who the viewer writes tags and notes as")
    s.add_argument("--terminal", choices=["auto", "here", "beside", "window", "app"], default="auto",
                   help="where Claude Code runs: here (this terminal), beside (in the viewer, beside the documents), "
                        "window (in the viewer, a window of its own), app (a new window of the system's terminal "
                        "program). auto: here when started from a terminal, else beside")
    s.add_argument("--hold", action="store_true", help="if the session fails, wait for Enter before exiting "
                                                       "(for a terminal window that would close)")
    s.add_argument("command", nargs="*", help="run this instead of claude (after --)")
    s = sub.add_parser("gui", help="open the launcher: make matters, add documents and open sessions without a command line")
    s.add_argument("--install", action="store_true", help="only add adhocdocs to the desktop's application menu")
    s = sub.add_parser("matters", help="the matters on this user's list (the launcher's), most recent first")
    s.add_argument("action", nargs="?", choices=["add", "forget"])
    s.add_argument("path", nargs="?")
    s = sub.add_parser("image", help="build the session image on this machine")
    s.add_argument("action", choices=["build"])
    s.add_argument("--no-cache", action="store_true")
    sub.add_parser("status", help="inside a session: the matter, mounts, network and services")
    s = sub.add_parser("view", help="start the document viewer for this matter")
    s.add_argument("--user", default=os.environ.get("USER", "user"), help="who the viewer writes tags and notes as")
    s.add_argument("--wait", action="store_true", help="run in the foreground")
    s = sub.add_parser("annotate-as-user", help=argparse.SUPPRESS)
    s.add_argument("--user", required=True)
    s = sub.add_parser("meta-pane", help=argparse.SUPPRESS)      # the viewer's "list parts" links (read only)
    s.add_argument("ref")
    s.add_argument("--of", action="store_true")
    s.add_argument("--splits", nargs="?", const="")
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
    s = sub.add_parser("fetch", help="a limited copy: keep documents' originals here (fetching them now), or stop",
                       description=FETCH_HELP, formatter_class=argparse.RawDescriptionHelpFormatter)
    s.add_argument("targets", nargs="*", help="document IDs, doc: locators, or list files (search --list)")
    s.add_argument("--unpin", action="store_true", help="stop keeping them: the cache may let them go")
    s = sub.add_parser("fetch-original", help=argparse.SUPPRESS)       # the viewer's: fetch these, or say why not
    s.add_argument("contents", nargs="+")
    s = sub.add_parser("purge", help="delete documents from the matter, here and on every installation (irreversible)",
                       description=PURGE_HELP, formatter_class=argparse.RawDescriptionHelpFormatter)
    s.add_argument("docs", nargs="+", help="document IDs (or doc: locators)")
    s.add_argument("--user", required=True)
    s.add_argument("--reason", required=True, help='e.g. "clawback: produced in error"')
    s.add_argument("--with-duplicates", action="store_true", help="also the documents that share their original")
    s.add_argument("--yes", action="store_true", help="don't ask first")
    s = sub.add_parser("purges", help="purges this installation has carried out, and what they leave for you to do")
    s.add_argument("--ack", metavar="PURGE", help="mark a purge's notice as dealt with")
    s.add_argument("--user")
    s = sub.add_parser("sync", help="syncing with this matter's other installations (docs/SYNC.md)",
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
    x.add_argument("--limited", action="store_true",
                   help="hold everything but the originals, fetching those when needed from a member that holds "
                        "them all (a hub, usually)")
    x = ss.add_parser("copy", help="make this installation a full copy (every original) or a limited one")
    x.add_argument("copy", choices=["full", "limited"])
    x.add_argument("--user", required=True)
    x.add_argument("--cache-gb", type=float, help="a limited copy's room for originals it fetched, in GB "
                                                    "(default 10)")
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
    s = sub.add_parser("hub", help="run a hub: an always-on member that holds many matters (docs/SYNC.md)",
                       description=HUB_HELP, formatter_class=argparse.RawDescriptionHelpFormatter)
    hs = s.add_subparsers(dest="action", required=True)
    x = hs.add_parser("init", help="make a hub in a directory")
    x.add_argument("dir")
    x.add_argument("--name", required=True, help="what members will see, e.g. \"Firm hub\"")
    x.add_argument("--address", action="append", default=[], help="host:port members reach it at; repeatable")
    x.add_argument("--plain", action="store_true",
                   help="hold matters readably (by default a hub holds them sealed: it can't read them)")
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
        gui.remember(m.root)
        out({"matter": str(m.root)})
        return
    if a.cmd == "gui":
        if a.install:
            out({"desktop_entry": str(gui.install_desktop_entry())})
            return
        sys.exit(gui.launch())
    if a.cmd == "matters":
        if a.action:
            if not a.path:
                p.error(f"matters {a.action}: needs a path")
            if a.action == "add":
                gui.remember(Matter.find(a.path).root)
            else:
                gui.forget(a.path)
        out(gui.matters())
        return
    if a.cmd == "hub":
        return _hub(a)
    if a.cmd == "sync" and a.action == "join":
        from . import invite
        r = invite.join(a.code, a.path, a.name, a.address, "limited" if a.limited else "full")
        gui.remember(a.path)
        out(r)
        return
    if a.cmd == "setup":
        from . import setup
        try:
            out(setup.setup(a.check, not a.no_viewer, not a.no_image, a.no_cache))
        except setup.SetupError as e:
            print(json.dumps({"error": str(e), **e.summary, **({"missing": e.missing} if e.missing else {})}), file=sys.stderr)
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
                sys.exit(_open(a, container))
        except (container.ContainerError, MatterNotFound, RuntimeError) as e:
            print(json.dumps({"error": str(e)}), file=sys.stderr)
            if getattr(a, "hold", False):
                _hold()
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
        gui.remember(m.root)

        def report(doc, name, ext, children):
            out({"doc": doc, "name": name, "kind": ext.kind,
                 "layers": [{"view": l.view, "chars": len(l.text)} for l in ext.layers],
                 "children": children})
        if a.manifest:
            ingest_manifest(m, a.manifest, report)
        else:
            ingest_paths(m, a.paths, a.custodian, a.collection, report)
    elif a.cmd in ("ingest-production", "overlay-production"):
        from . import received
        overrides = {}
        for x in a.map:
            if "=" not in x:
                raise ValueError(f"--map {x}: give FIELD=COLUMN")
            k, v = x.split("=", 1)
            overrides[k.strip()] = v.strip()
        if a.cmd == "overlay-production":
            out(received.overlay(m, a.dat, a.volume, overrides, a.day_first, check=a.check))
            return
        kw = {"dat": a.dat, "opt": a.opt, "overrides": overrides, "day_first": a.day_first}
        if a.check:
            out(received.check(a.volume, m=m, name=a.name, replaces=a.replaces, **kw))
            return
        if not a.party:
            raise ValueError("--party: who produced this volume (or --check to look first)")
        gui.remember(m.root)
        r = received.ingest(m, a.volume, a.party, a.name, replaces=a.replaces,
                            person="user:" + a.user.removeprefix("user:"), **kw)
        r.pop("docs")
        out(r)
    elif a.cmd == "withdraw":
        doc = locators.parse("doc:" + a.doc.removeprefix("doc:"), m.objects)["doc"]
        out(m.logs["store"].append(op="withdraw", doc=doc, reason=a.reason))
    elif a.cmd == "view":
        out({"viewer": viewmod.launch(m, a.user, a.wait), "socket": str(viewmod.socket_path(m))})
    elif a.cmd == "meta-pane":
        # The viewer's own "list parts" links: a list pane's files, as `view meta` makes them (host only, reads only).
        header, items = meta_list(m, a.ref, a.of, a.splits)
        files, sequence = viewmod.list_html(m, (header, items))
        out({"files": files, "sequence": sequence, "title": header["title"]})
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
    elif a.cmd in ("fetch", "fetch-original"):
        from . import fetch
        if a.cmd == "fetch-original":
            r = fetch.fetch(m, a.contents)
            out(r)
            sys.exit(1 if r["unavailable"] else 0)
        if not a.targets:
            out(fetch.status(m))
            return
        docs = []
        for t in a.targets:
            if Path(t).is_file():
                docs += [lists.doc_of(it) for it in lists.read(t)[1]]
            else:
                docs.append(locators.parse("doc:" + t.removeprefix("doc:").split("#")[0], m.objects)["doc"])
        out(fetch.pin(m, docs, unpin=a.unpin))
    elif a.cmd == "purge":
        from . import purge
        docs = [locators.parse("doc:" + d.removeprefix("doc:"), m.objects)["doc"] for d in a.docs]
        p = purge.preview(m, docs, a.with_duplicates)
        if not a.yes:
            print(f"This deletes {len(p['docs'])} document(s) (with attachments), {len(p['objects'])} stored "
                  f"object(s), and redacts {p['events']} tag/note event(s), on this installation and every "
                  "installation of the matter. It can't be undone. Type PURGE to go on: ", end="", file=sys.stderr)
            if input().strip() != "PURGE":
                raise ValueError("not purged")
        out(purge.purge(m, f"user:{a.user}", docs, a.reason, a.with_duplicates))
    elif a.cmd == "purges":
        from . import purge
        if a.ack:
            if not a.user:
                raise ValueError("--ack needs --user")
            out(purge.acknowledge(m, a.ack, f"user:{a.user}"))
        else:
            for n in purge.notices(m):
                out(n)
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


PURGE_HELP = """\
Delete documents from the matter for good: a clawback, or destruction under
a protective order. On this installation, and on every installation of the
matter (and the hub) as they sync: originals, text, metadata, the
documents' attachments, layers made from them, index entries, and the tags
and notes on them (their events are redacted). A record of the purge stays:
who, when, why, which document IDs.

What adhocdocs doesn't track it can't purge: the workspace (drafts, exports,
productions), Claude's transcripts, copies made elsewhere. Every
installation gets a notice listing what it found there (adhoc purges).

Only a person can purge, on the host."""


FETCH_HELP = """\
On a limited copy (adhoc sync copy limited), originals are fetched when
something reads them, from a member that holds them all, and kept in a cache
that lets the least recently used go when it's full. To work through many
documents' originals at once (re-OCR, decoding raw bytes, a production),
fetch them first: they're kept until unpinned.

  search 'kind:other' --list other.jsonl ; adhoc fetch other.jsonl
  adhoc fetch <doc> <doc>…            keep these (and their attachments)
  adhoc fetch --unpin other.jsonl     the cache may let them go again
  adhoc fetch                         the cache: its size, limit and what's pinned

Documents ingested here stay until a full member holds them."""


SYNC_HELP = """\
Sync this matter with its other installations: laptops, desktops, a hub.
Every installation keeps a full copy and works offline, unless it's a
limited copy: then it holds everything but the originals, and fetches those
from a member that holds them all. Documents, tags,
notes, entities and published work sync; the workspace doesn't. See docs/SYNC.md.

  adhoc sync invite --user me --address myhost:7480   (on a member) prints a code
  adhoc sync serve --listen 0.0.0.0:7480              (on a member) to be reached
  adhoc sync join <code> ~/matters/smith --name "Bob's desktop"   (the new one)
  adhoc sync join <code> ~/matters/smith --limited    (no originals, but on demand)
  adhoc sync copy full|limited --user me              change what this one holds
  adhoc sync now                                      sync with every member
  adhoc sync status | members | conflicts

An invitation is a password: send it the way you'd send one."""


def _sync(m, a):
    from . import invite, lan as lanmod, peer, pool, sync
    if a.action == "status":
        st = sync.status(m)
        st["members"] = pool.Pool.load(m).summary(st["origin"])
        if pool.founded(m):
            from . import fetch
            st["copy"] = "full" if peer.holds_all(m) else "limited"
            st["originals_missing"] = len(peer.missing_originals(m))
            if st["copy"] == "limited":
                st["cache"] = fetch.status(m)
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
    elif a.action == "copy":
        from . import fetch
        if a.cache_gb is not None:
            fetch.set_limit(m, a.cache_gb)
        ev = pool.set_copy(Matter(m.root), f"user:{a.user}", a.copy)
        fetch.evict(Matter(m.root))
        out({"copy": a.copy, "changed": ev is not None,
             "note": "Originals this installation lacks come from the others as it next syncs (adhoc sync now)."
             if a.copy == "full" else
             "New originals from the others won't be fetched; those here stay. Others fetch from a full member."})
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
no sessions, and it can't read the matters it holds: members send it
everything encrypted with a key only they have (adhocdocs.seal; --plain
turns that off).

  adhoc hub init /srv/adhoc-hub --name "Firm hub" --address hub.firm.example:7480
  adhoc hub enroll /srv/adhoc-hub --person alice     prints a code for Alice
  adhoc hub serve /srv/adhoc-hub                     (keep it running)

Alice, on her machine:  adhoc --matter ~/matters/smith sync push <code> --user alice

A hub code lets its holder put matters on the hub; it's a password.
Matters already on the hub are governed by their own pools."""


def _hub(a):
    from . import hub, peer
    if a.action == "init":
        h = hub.Hub.init(a.dir, a.name, a.address, sealed=not a.plain)
        out({"hub": str(h.root), "name": a.name, "addresses": a.address, "origin": h.identity.origin,
             "sealed": not a.plain})
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
    if not state.layers.get(content):
        # say so, and why, rather than print nothing
        meta = m.objects.get_object(state.metadata[h]) if h in state.metadata else {}
        kids = state.children.get(h, [])
        why = (f"a container: its text is in its {len(kids)} parts (`meta show family:{h}`)" if kids else
               "no text was extracted: a scan with no text layer, OCR not run or failed (`layers ocr`), "
               "or a format nothing reads (`raw info`)" if meta.get("kind") in ("pdf", "image") else
               "no text was extracted: a format nothing reads (`raw info`)")
        out({"doc": h, "layers": 0, "kind": meta.get("kind"), "why": why})
        return
    for l in state.layers.get(content, []):
        obj = m.objects.get_object(l.id)
        out({"layer": l.id, "view": l.view, "primary": primary is not None and l.id == primary.id,
             "chars": len(m.objects.get_text(l.text)), "text_path": str(m.objects.path(l.text)),
             "parts": sorted(k for k in ("segments", "geometry", "presentation") if k in obj),
             "feed": l.feed})
    if kids := state.children.get(h):
        # a mailbox, an email with attachments, an archive: the parts are documents of their own
        out({"parts": len(kids), "see": f"meta show family:{h}"})


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
    from .portable import tesseract
    if not shutil.which(tesseract()):
        raise ValueError("OCR needs tesseract, which isn't installed where this runs. Tell the user; "
                         "`adhoc setup` on the host checks for it. Until then, read the page as a picture "
                         "(`raw path`), and keep what you read as an interpretation, each field linked to its page "
                         "region (`interp --help`, \"A reading of a scan\"), so it is shown as your reading, not the "
                         "document's text")
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
    from .received import NAMES, resolve_name
    from . import metadocs
    if metadocs.is_ref(a.locator):                 # a metadocument: each of its parts
        for part in metadocs.members(m, a.locator):
            r = locators.resolve(m, part)
            out({"loc": locators.format(r["loc"]) if r.get("loc") else locators.format(part), "status": r["status"],
                 **({"text": r["text"]} if a.json and "text" in r else {})})
        return
    if a.locator.startswith(NAMES):
        for hit in resolve_name(m, a.locator):
            r = locators.resolve(m, hit["loc"])
            out({**hit, "status": r["status"], **({"text": r["text"]} if a.json and "text" in r else {})})
        return
    r = locators.resolve(m, a.locator)
    if a.json:
        out(dict(r))
        return
    if r["status"] == "broken":
        raise locators.LocatorError(f"broken locator: {r['error']}")
    if "bytes" in (r["loc"].get("select") or {}):            # an original's bytes, as a hex dump
        from .raw import DUMP_LIMIT, hexdump_text
        a0 = r["loc"]["select"]["bytes"][0]
        print(hexdump_text(r.data[:DUMP_LIMIT], a0))
        if len(r.data) > DUMP_LIMIT:
            print(json.dumps({"truncated": f"showing {DUMP_LIMIT} of {len(r.data)} bytes"}), file=sys.stderr)
        return
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


# -- raw and interp: files of unknown format or schema ------------------------------------

RAW_HELP = """\
Originals as bytes, and structured data as structure: for files no
extractor reads (kind "other"), and for JSON, JSON Lines and XML whose
schema no one has told us. See docs/DATA.md.

  raw info <doc>                     size, hash, signature, entropy, text-likeness, format
  raw hex <doc|locator> [--start N] [--length N]
                                     a hex dump; prints the byte locator of the range
  raw strings <doc> [--min N] [--limit N]
                                     printable runs (ASCII, UTF-16LE), each with its byte locator
  raw schema <doc>                   JSON/JSON Lines/XML: every path, its types, counts and
                                     examples (each with a ptr= or xpath= locator)
  raw path <doc>                     the original's path, read-only, for your own decoders

Byte locators (doc:<id>#bytes=start,end) quote an original's bytes; pages
show them as hex (::q{#id}). ptr= locators (doc:<id>#ptr=/items/3/amount,
RFC 6901) name a value in JSON, and xpath= locators
(doc:<id>#xpath=/orders/order[2]/sku, or …/order[@id='7']/@ref) an element
or attribute in XML; both resolve to its text, which quotes verbatim like
any text. In a spreadsheet or CSV, col=<header>&row=N names cells by the
column's header (row= may be 7-9; col alone is the whole column; header=N
if the header isn't row 1). What bytes or fields *mean* is interpretation:
write a decoder and record it with `interp run`.
"""


@run
def raw(argv=None):
    from . import raw as rawmod, structured
    p = parser("raw", RAW_HELP)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("info")
    s.add_argument("doc")
    s = sub.add_parser("hex")
    s.add_argument("loc", help="a document, or a byte locator (doc:…#bytes=a,b)")
    s.add_argument("--start", type=lambda x: int(x, 0), default=0, help="first byte (0x… works)")
    s.add_argument("--length", type=lambda x: int(x, 0), default=256)
    s.add_argument("--json", action="store_true", help="rows as JSON instead of text")
    s = sub.add_parser("strings")
    s.add_argument("doc")
    s.add_argument("--min", type=int, default=6, help="shortest run (characters)")
    s.add_argument("--limit", type=int, default=2000)
    s.add_argument("--start", type=lambda x: int(x, 0), default=0)
    s.add_argument("--length", type=lambda x: int(x, 0), default=None, help="default: to the end (at most 64 MB)")
    s = sub.add_parser("schema", help="what structured data holds: JSON, JSON Lines and XML path by path; "
                                      "spreadsheets and CSV column by column")
    s.add_argument("doc")
    s.add_argument("--paths", type=int, default=500, help="most paths to report")
    s.add_argument("--header", type=int, help="spreadsheets: the header row (default: found)")
    s = sub.add_parser("path")
    s.add_argument("doc")
    matter_everywhere(sub)
    a = p.parse_args(argv)
    m = Matter.find(a.matter)

    def doc_of(x):
        return locators.parse("doc:" + x.removeprefix("doc:").split("#")[0], m.objects)["doc"]

    if a.cmd == "info":
        out(rawmod.info(m, doc_of(a.doc)))
    elif a.cmd == "path":
        doc = doc_of(a.doc)
        print(m.objects.path(rawmod.content_of(m, doc)))
    elif a.cmd == "hex":
        loc = locators.parse(a.loc if a.loc.lstrip().startswith("{") else "doc:" + a.loc.removeprefix("doc:"), m.objects)
        content = rawmod.content_of(m, loc["doc"])
        if "bytes" not in (loc.get("select") or {}):
            n = rawmod.size(m, content)
            start = min(a.start, n)
            loc = rawmod.byte_locator(loc["doc"], m, start, min(n, start + min(a.length, rawmod.DUMP_LIMIT)), content)
        r = locators.resolve(m, loc)
        if r["status"] == "broken":
            raise locators.LocatorError(f"broken locator: {r['error']}")
        data = r.data[:rawmod.DUMP_LIMIT]
        start = r["loc"]["select"]["bytes"][0]
        if a.json:
            for row in rawmod.hexdump(data, start):
                out(row)
        else:
            print(rawmod.hexdump_text(data, start))
        print(json.dumps({"loc": locators.format(r["loc"]), "quote": r["loc"]["quote"]}), file=sys.stderr)
    elif a.cmd == "strings":
        doc = doc_of(a.doc)
        content = rawmod.content_of(m, doc)
        n = rawmod.size(m, content)
        end = min(n, a.start + (a.length if a.length is not None else 64 << 20))
        data = rawmod.read(m, content, a.start, end)
        for s0, e0, text_, enc in rawmod.strings(data, a.min, a.start, a.limit):
            out({"loc": locators.format({"doc": doc, "select": {"bytes": [s0, e0]}}), "encoding": enc, "text": text_})
        if end < n:
            print(json.dumps({"note": f"read bytes {a.start}–{end} of {n}; use --start/--length for the rest"}), file=sys.stderr)
    elif a.cmd == "schema":
        doc = doc_of(a.doc)
        content = rawmod.content_of(m, doc)
        layer = m.state.primary_layer(content)
        segs = m.objects.get_jsonl(m.objects.get_object(layer.id).get("segments")) if layer else []
        if any(g.get("kind") == "sheet" for g in segs):
            sheets = structured.table_schema(m.objects.get_text(layer.text), segs, a.header)
            for k, sh in enumerate(sheets):
                for col in sh["columns"]:
                    for ex in col["examples"]:
                        sel = {"sheet": sh["sheet"]} if k and sh["sheet"] else {}
                        if col["header"] and not col.get("header_repeats"):
                            sel.update(col=col["header"], row=ex.pop("row"),
                                       **({"header": sh["header_row"]} if sh["header_row"] != 1 else {}))
                        else:
                            sel["cells"] = f"{col['col']}{ex.pop('row')}"
                        ex["loc"] = locators.format({"doc": doc, "select": sel})
                out({"doc": doc, "format": "spreadsheet", **sh})
            return
        kind = locators.pointer_kind(m, doc)
        layer = m.state.primary_layer(content)
        if kind is None and layer is not None:
            kind = structured.sniff(m.objects.get_text(layer.text))
        if kind is None:
            meta = m.objects.get_object(m.state.metadata[doc]) if doc in m.state.metadata else {}
            kind = "xml" if meta.get("kind") == "xml" or meta.get("format") == "xml" else None
        if kind == "xml":
            res, key = structured.xml_schema(rawmod.read(m, content), a.paths), "xpath"
        elif kind in ("json", "ndjson") and layer is not None:
            text_ = m.objects.get_text(layer.text)
            res, key = structured.schema(structured.loads(text_, kind), kind == "ndjson", a.paths), "ptr"
        else:
            raise ValueError("raw schema is for JSON, JSON Lines, XML, spreadsheets and CSV; "
                             "for other formats see raw info, raw strings, raw hex")
        for row in res["paths"]:
            for ex in row.get("examples", []):
                ex["loc"] = locators.format({"doc": doc, "select": {key: ex.pop(key)}})
        out({"doc": doc, "format": kind, **res})


INTERP_HELP = """\
Interpretations: your own readings of documents whose format or schema
adhocdocs doesn't know, kept apart from the documents' own text. See docs/DATA.md.

  interp run <doc>... --name N --title T [--about A] [--type T] [--replace] -- <command> [args...]
        run a decoder you wrote on the originals and record what it prints:
        JSON (a table, tree or value), CSV, text, or a chart as SVG or PNG.
        It gets the first original on stdin and their paths in $ADHOC_INPUT
        and $ADHOC_INPUTS (JSON). Files on its command line are copied in
        and run from the copies, so the record says exactly what ran.
  interp ls                          the interpretations, newest first
  interp show N                      the record, and the start of the output
  interp check [N]                   still holds (inputs, files) and reruns to the same output
  interp search QUERY [--name N]...  search the outputs, with search's query language (no field
                                     filters); /p means the same record. Hits are places in your
                                     readings, each with the evidence locator its record gives
                                     (--list F writes them as a list file, for view list F:
                                     captioned as your readings; each row opens its evidence)
  interp render N [--ptr P] [--as A] [-o out.html]
  interp view N [--ptr P] [--as A] [--pane P]   show it in the viewer

--about says what you think the file is and how sure you are; it is shown
with the interpretation. In JSON output, objects with "_bytes": [a, b]
(bytes of the first input), "_ptr" (a JSON pointer into it) or "_loc" link
to the evidence they were read from, which can be quoted. In pages, show an
interpretation with :interp{#id} or ::interp{#id as=table|tree|value|text|image}
(page ref add page.md <id> --interp N [--ptr P]); it can never be quoted
with :q. Say it is your reading whenever you rely on one.

A reading of a scan: where a page has no text layer, or OCR mangled it
(handwriting, stamps), and you read it from the picture instead, keep what
you read here rather than only in chat. Write it as JSON, each field with
the page region it came from ("_loc": "doc:D#page=1&xywh=x,y,w,h", fractions
of the page), then `interp run D --name rdr-reading --title "…" --about
"read by eye from the page image" -- cat reading.json`. The user opens each
field on its region, and `interp search` finds it.
"""


@run
def interp(argv=None):
    from . import interp as im
    p = parser("interp", INTERP_HELP)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("run")
    s.add_argument("docs", nargs="+")
    s.add_argument("--name", required=True)
    s.add_argument("--title", required=True, help="what the output is, e.g. 'Call records decoded from the .cdr file'")
    s.add_argument("--about", help="what you think the file is, why, and how sure you are")
    s.add_argument("--type", choices=im.TYPES, help="the output's type (default: guessed)")
    s.add_argument("--replace", action="store_true")
    sub.add_parser("ls")
    s = sub.add_parser("show")
    s.add_argument("name")
    s = sub.add_parser("check")
    s.add_argument("name", nargs="?")
    s.add_argument("--no-rerun", action="store_true")
    s = sub.add_parser("search", help="search interpretations' outputs (search's query language, no field filters)")
    s.add_argument("query")
    s.add_argument("--name", action="append", help="only this interpretation (repeatable)")
    s.add_argument("--limit", type=int, help="most hits to print per interpretation")
    s.add_argument("--list", dest="list_file", help="also write the hits as a list file, for view list")
    for name in ("render", "view"):
        s = sub.add_parser(name)
        s.add_argument("name")
        s.add_argument("--ptr", help="a JSON pointer into the output")
        s.add_argument("--as", dest="as_", choices=im.SHOWN_AS)
        if name == "render":
            s.add_argument("-o", "--out")
        else:
            s.add_argument("--pane", help="replace this pane's content")
            s.add_argument("--window")
    matter_everywhere(sub)
    argv = list(sys.argv[1:] if argv is None else argv)
    command = []
    if "--" in argv:                       # the decoder's own command line, untouched
        i = argv.index("--")
        argv, command = argv[:i], argv[i + 1:]
    a = p.parse_args(argv)
    m = Matter.find(a.matter)
    try:
        if a.cmd == "run":
            cmd = command
            rec = im.run(m, a.docs, a.name, cmd, a.title, a.about, a.type, a.replace)
            out({"name": rec["name"], "folder": str(im.folder(m, rec["name"])), "output": rec["output"],
                 "decoder": rec["decoder"]})
        elif a.cmd == "ls":
            recs = [im.load(m, n) for n in im.names(m)]
            for rec in sorted(recs, key=lambda r: r.get("made", ""), reverse=True):
                out({"name": rec["name"], "title": rec["title"], "type": rec["output"]["type"], "made": rec.get("made"),
                     "inputs": [i["doc"] for i in rec["inputs"]]})
        elif a.cmd == "show":
            rec = im.load(m, a.name)
            data = im.output_bytes(m, rec)
            preview = None if rec["output"]["type"] == "png" else data[:2000].decode("utf-8", errors="replace")
            out({**rec, "folder": str(im.folder(m, a.name)), "preview": preview})
        elif a.cmd == "check":
            bad = False
            for n in [a.name] if a.name else im.names(m):
                r = im.check(m, n, rerun=not a.no_rerun)
                bad = bad or not r["ok"]
                out(r)
            if bad:
                sys.exit(1)
        elif a.cmd == "search":
            header, items = im.search(m, a.query, a.name, a.limit)
            if a.list_file:
                lists.write(a.list_file, *im.search_list(header, items))
                header = {**header, "list": a.list_file}
            out(header)
            for it in items:
                out(it)
        elif a.cmd == "render":
            doc, _ = im.page_html(m, a.name, a.ptr, a.as_)
            if a.out:
                Path(a.out).write_text(doc, encoding="utf-8", newline="\n")
                out({"written": a.out})
            else:
                sys.stdout.write(doc)
        elif a.cmd == "view":
            import base64
            doc, images = im.page_html(m, a.name, a.ptr, a.as_, inline_images=False)
            files = {"index.html": base64.b64encode(doc.encode()).decode(),
                     **{k: base64.b64encode(v).decode() for k, v in images.items()}}
            rec = im.load(m, a.name)
            c = {"cmd": "pane", "files": files, "title": "Interpretation: " + rec["title"], "update": a.pane,
                 "window": a.window}
            out(viewmod.command(m, {k: v for k, v in c.items() if v is not None}))
    except im.InterpError as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)


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
search "<query>" --grain new|thread|family|copies|versions|bundle
                                                     results on metadocuments, not documents

--grain re-reads the results at another scale (docs/METADOCS.md): one line per
thread, family, item however many copies, or text however many drafts, or
bundle. --grain new is one line per thing someone wrote: copies count once,
and a hit in quoted text counts for the message that wrote it (quoted_in
lists where it was quoted), so an email quoted in 40 replies is one result.

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
    p.add_argument("--grain", type=_grain_arg(("doc", "new", "thread", "family", "copies", "versions", "bundle")), default="doc",
                   help="report metadocuments: what each message adds, threads, families, copies, versions, bundles")
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
    if a.grain != "doc":
        if a.by == "hit":
            p.error("--grain reports metadocuments; it doesn't go with --by hit")
        from . import metadocs
        limit, s.opts.limit = s.opts.limit, None
        header, items = s.finish(header, items)
        items = metadocs.regroup(m, items, a.grain)
        header["grain"] = a.grain
        header["matches"]["units"] = len(items)
        if limit is not None:
            items = items[:limit]
        out(header)
        for it in items:
            out(it)
        if a.list_file:
            lists.write(a.list_file, *(lambda rows: (rows[0], rows[1:]))(metadocs.to_list(header, items)))
        return
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
    s.add_argument("doc", help="the item: its document, its whole locator (when the list holds several "
                               "passages of one document), or @N for the Nth item")
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
            key = a.doc if a.doc.startswith("@") else _item_key(items, a.doc) or doc(a.doc)
            sets.set_fields(items, key, values, header)
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


def _item_key(items, given):
    """The ``loc`` of the item a whole locator names (same document and
    selection, however it is spelled), for lists holding several passages of
    one document; None when it names none, or is a bare document."""
    try:
        want = locators.parse(given)
    except (locators.LocatorError, ValueError):
        return None
    if not want.get("select"):
        return None
    for it in items:
        for x in [it, *it.get("children", [])]:
            try:
                got = locators.parse(x["loc"])
            except (locators.LocatorError, ValueError):
                continue
            if got["doc"] == want["doc"] and got.get("select") == want["select"]:
                return x["loc"]
    return None


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

Tags and notes on a document a replacement volume superseded are carried
over to the replacement, marked ``carried`` (docs/RECEIVED.md): they were about
the old document, and the replacement may differ. List them with
``ann carried``; once you've checked one still holds, and it was yours,
``ann confirm-carried TAG LOCATOR`` (or ``--note ID``). The user's are the
user's to confirm.

Entities (the entity layer; docs/ENTITIES.md): people, organizations, defined
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

Events with a --date go on the viewer's date cards (docs/DATES.md): hovering a
date in a document shows the nearest events before and after it. Add the
events that turn the story (a dozen or so, not every document) when you
write a chronology, or when the user asks what matters when.

Claude can't change a field, remove an alias or delete an entity the user
set, made or added; it can fill in what the user left blank.

People who appear only in email headers, or in the text, aren't entities
until someone adds them, so their names aren't marked and the graph can't
say who emailed whom. `ann people` lists them from the cast (`analyze
people`), each with its addresses, counts and roles, and whether it looks
like a person, a group address or a bulk sender:

  ann people [--all]                                  who isn't an entity yet
  ann add-people "Lena Fairbanks" "Ivy Castellano"    add these, names and addresses as aliases
  ann add-people --top 10                             the ten most active
  ann add-people --known                              give known entities the addresses they lack

Add the people who matter to the matter, not everyone in the To line; then
`graph --email` and `view graph --email` show who corresponds with whom.
The summary they get is the counts and roles; write a real one with
edit-entity when you know who they are.

Links (docs/GRAPH.md): how two entities are connected, and on what evidence. They
make the entity list a graph: the viewer's cards show them and move from
one entity to the next, `graph` queries them, and `view graph` draws them.

  ann link "Rita Ames" "signed for" "Dock 4 delivery" --see <locator> [--date 2026-03-09] [--summary …]
  ann links [--entity E] [--rel R] [--facet F]       links, with both entities' names
  ann edit-link <link-id> --see <locator> | --rel … | --summary …
  ann delete-link <link-id>

The rel reads from the first entity to the second ("works for", "signed",
"sent", "paid", "party to", "approved"). Record a link when the documents
show it, with --see pointing at the words; one with no evidence is listed by
`graph unsupported`. Don't record co-mentions (two names in one document) as
links: `graph --cooccur` derives those, marked as leads.

Facets: named issues ("delivery", "damages", "notice") that entities, links,
notes and tags are filed under, so the user (and you) can see only what bears
on the issue at hand. The viewer's facet picker (the ◇ button, or F) dims
the rest.

  ann define-facet delivery --description "Were the goods delivered and accepted?"
  ann file delivery --entity "Rita Ames" --link <id> --note <id> --tag pod
  ann unfile delivery --note <id>
  ann facets                                         each facet with what is filed under it
  ann facet delivery                                 everything filed under one
  ann notes --facet delivery / ann entities --facet delivery / ann links --facet delivery

Filing doesn't change the thing filed, so you can file the user's notes;
you can't unfile what the user filed. A reply is filed with its thread, and
a tag's applications with the tag.

Bundles (docs/METADOCS.md): documents, or parts of them, treated as one thing:
"the Q3 board pack", the pages of a merged PDF that were one letter, the
reply above the quoted text, a thread. A tag or note on a bundle is on it as
a whole, once; filters (search --tag, ann find) count it for every
document in it. Anywhere a locator goes, a metadocument can too: thread:D,
family:D, copies:D, versions:D, new:D (what message D adds, without what it
quotes) are each of their parts, and bundle:B is the bundle (with --parts,
each part).

  ann bundle "Board pack Q3" doc:3fa2… doc:91bc… --summary "Sent to the board Oct 2"
  ann bundle "Kettering letter" "doc:77aa…#page=4-9"         part of a merged PDF
  ann bundle "Past-due thread" thread:3fa2…                   what's in the thread now
  ann tag hot bundle:"Board pack Q3"     ann note bundle:b_19… --body …
  ann tag responsive thread:3fa2…                             each message, not one bundle
  ann edit-bundle "Board pack Q3" --add doc:… --remove doc:…
  ann bundles [NAME] [--doc D]

Privilege calls go on documents, not bundles: a production withholds
documents (ann tag privileged bundle:B --parts is the user's to do).
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


def _targets(m, locs, family=False, dupes=False, parts=False, units=None):
    """Targets from locators. ``bundle:B`` is the bundle as one target (or,
    with ``parts``, each of its parts); a metadocument (``thread:D``,
    ``family:D``, ``new:D``…) is each of its parts."""
    from . import metadocs
    state = m.state
    out = []
    for s in locs:
        if isinstance(s, str) and s.startswith("bundle:") and not parts:
            out.append({"bundle": s[7:].strip()})
            continue
        if metadocs.is_ref(s):
            units = units or metadocs.Units(m)
            if s.startswith("bundle:"):
                s = "bundle:" + units.bundle(s[7:])["bundle"]
            got = units.members(s)
            if (family or dupes) and any(g.get("select") for g in got):
                raise ValueError("--family and --dupes apply to whole documents")
            out += _targets(m, [locators.format(g) if g.get("select") else g["doc"] for g in got], family, dupes)
            continue
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
    s.add_argument("--facet", help="only notes filed under this facet (with their threads)")
    s = sub.add_parser("find", help="documents by tags: --tag hot --tag -privileged")
    s.add_argument("--tag", action="append", required=True)
    s.add_argument("--list", dest="list_file", help="also write a list file")
    s = sub.add_parser("log", help="the event log, all installations' logs merged")
    s.add_argument("--since", help="a cursor from --cursor (JSON), or an event ID. Events synced from other "
                   "installations can sort before ones you've read; a cursor still catches them, an ID doesn't")
    s.add_argument("--cursor", action="store_true", help="print only the cursor for the log's end")
    s.add_argument("--author", help="user, claude, or user:<id>")
    s = sub.add_parser("carried", help="tags and notes carried over from a superseded document, not yet confirmed")
    s.add_argument("--target")
    s = sub.add_parser("confirm-carried", help="a carried-over tag (TAG LOCATOR) or note (--note ID) stands: unmark it")
    s.add_argument("tag", nargs="?")
    s.add_argument("locator", nargs="?")
    s.add_argument("--note")
    s.add_argument("--requested-by")
    s = sub.add_parser("check", help="check a note's references and quotations (note ID, or - for a body on stdin)")
    s.add_argument("note")
    for name in ("tag", "untag"):
        s = sub.add_parser(name)
        s.add_argument("tag")
        s.add_argument("locators", nargs="+")
        s.add_argument("--family", action="store_true", help="every document in each one's family")
        s.add_argument("--dupes", action="store_true", help="every exact duplicate of each one")
        s.add_argument("--parts", action="store_true", help="bundle:B: each of its parts, not the bundle as one")
        s.add_argument("--requested-by")
    s = sub.add_parser("bundle", help="make a bundle: documents, or parts of them, treated as one (tag or note it as one)")
    s.add_argument("name")
    s.add_argument("parts", nargs="*", help="locators, or metadocuments: thread:D, family:D, versions:D, copies:D, new:D")
    s.add_argument("--list", dest="list_file", help="also every item in this list file")
    s.add_argument("--summary", help="what it is, in a line")
    s.add_argument("--facet", action="append", help="file it under this facet too (repeatable)")
    s.add_argument("--requested-by")
    s = sub.add_parser("edit-bundle")
    s.add_argument("bundle", help="ID, ID prefix or name")
    s.add_argument("--name")
    s.add_argument("--summary")
    s.add_argument("--add", action="append", default=[], help="parts to add (locators or metadocuments)")
    s.add_argument("--remove", action="append", default=[], help="parts to remove")
    s.add_argument("--requested-by")
    s = sub.add_parser("delete-bundle")
    s.add_argument("bundle")
    s.add_argument("--requested-by")
    s = sub.add_parser("bundles", help="the bundles, or one with its parts, tags and notes")
    s.add_argument("ref", nargs="?")
    s.add_argument("--doc", help="only bundles holding this document")
    s.add_argument("--facet")
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
    s.add_argument("--facet", help="only entities filed under this facet")
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
    s = sub.add_parser("people", help="people from email headers and names in the text, and whether each is an entity")
    s.add_argument("--all", action="store_true", help="also those already entities, and groups and bulk senders")
    s.add_argument("--min-docs", type=int, default=1, help="named in, or emailing, at least this many documents")
    s.add_argument("--limit", type=int, default=50)
    s = sub.add_parser("add-people", help="add people from `ann people` as entities, with their names and addresses as aliases")
    s.add_argument("names", nargs="*", help="names or addresses from `ann people` (default: none; see --top)")
    s.add_argument("--top", type=int, help="the N most active people who aren't entities")
    s.add_argument("--known", action="store_true",
                   help="also give entities already known the addresses and names they lack (for graph --email)")
    s.add_argument("--kind", default="person", help="the kind for those added (default person)")
    s.add_argument("--requested-by")
    s = sub.add_parser("link", help="record how two entities are connected: FROM REL TO, with --see evidence")
    s.add_argument("frm", metavar="FROM", help="an entity (ID, name or alias)")
    s.add_argument("rel", help='what FROM is or did to TO: "works for", "signed", "paid"')
    s.add_argument("to", metavar="TO")
    s.add_argument("--see", action="append", help="a locator to the evidence (repeatable)")
    s.add_argument("--summary", help="a line on the link, in your own words")
    s.add_argument("--date", help="when (YYYY-MM-DD, YYYY-MM or YYYY)")
    s.add_argument("--facet", action="append", help="file it under this facet too (repeatable)")
    s.add_argument("--requested-by")
    s = sub.add_parser("edit-link")
    s.add_argument("link")
    s.add_argument("--rel")
    s.add_argument("--see", action="append", help="replaces the evidence (repeatable)")
    s.add_argument("--add-see", action="append", help="adds to the evidence (repeatable)")
    s.add_argument("--summary")
    s.add_argument("--date")
    s.add_argument("--requested-by")
    s = sub.add_parser("delete-link")
    s.add_argument("link")
    s.add_argument("--requested-by")
    s = sub.add_parser("links", help="links between entities, with their names")
    s.add_argument("--entity", help="only links to or from this entity")
    s.add_argument("--rel", help="only links whose relation contains this")
    s.add_argument("--facet")
    for name in ("define-facet", "edit-facet"):
        s = sub.add_parser(name)
        s.add_argument("facet")
        s.add_argument("--description", help="the issue, in a line: what bears on it")
        s.add_argument("--color")
        s.add_argument("--requested-by")
    s = sub.add_parser("facets", help="the facets, with how much is filed under each")
    s = sub.add_parser("facet", help="everything filed under one facet")
    s.add_argument("facet")
    for name in ("file", "unfile"):
        s = sub.add_parser(name, help=("file" if name == "file" else "unfile") + " entities, links, notes or tags under a facet")
        s.add_argument("facet")
        s.add_argument("--entity", action="append", default=[])
        s.add_argument("--link", action="append", default=[])
        s.add_argument("--note", action="append", default=[])
        s.add_argument("--bundle", action="append", default=[])
        s.add_argument("--tag", dest="tags", action="append", default=[])
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
            for app in an.applications(parts=set(t) == {"doc"}):     # a document: with its bundles' tags
                if (annotations.target_key(app["target"]) == annotations.target_key(t)
                        or (set(t) == {"doc"} and app["target"].get("doc") == t["doc"])):
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
        notes = [n for n in an.notes.values() if who(a.by, n["by"]) and an.in_facet(a.facet, {"note": n["note"]})
                 and (t is None or annotations.target_key(n["target"]) == annotations.target_key(t)
                      or (set(t) == {"doc"} and n["target"].get("doc") == t["doc"]))]
        ids = {n["note"] for n in notes}
        def thread(n, depth):
            out({**n, "depth": depth})
            for r in notes:
                if r["parent"] == n["note"]:
                    thread(r, depth + 1)
        for n in notes:
            if n["parent"] not in ids:
                thread(n, 0)
    elif a.cmd == "carried":
        an = annotations.Annotations.load(m)
        t = _targets(m, [a.target])[0] if a.target else None
        on = lambda x: t is None or annotations.target_key(x) == annotations.target_key(t) or \
            (set(t) == {"doc"} and x.get("doc") == t["doc"])
        for app in an.applications():
            if app.get("carried") and on(app["target"]):
                out(app)
        for n in an.notes.values():
            if n.get("carried") and on(n["target"]):
                out(n)
    elif a.cmd == "confirm-carried":
        req = {"op": a.cmd, "requested_by": a.requested_by}
        if a.note:
            req["note"] = a.note
        elif a.tag and a.locator:
            req.update(tag=a.tag, target=_targets(m, [a.locator])[0])
        else:
            raise ValueError("confirm-carried TAG LOCATOR, or --note ID")
        out(annapi.request(m, {k: v for k, v in req.items() if v is not None}))
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
    elif a.cmd in ("people", "add-people"):
        _people(m, a)
    elif a.cmd in ("bundle", "edit-bundle", "delete-bundle", "bundles"):
        _bundles(m, a)
    elif a.cmd in ("link", "edit-link", "delete-link", "links", "define-facet", "edit-facet", "facets", "facet",
                   "file", "unfile"):
        _graph_writes(m, a)
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
            for t in _targets(m, a.locators, a.family, a.dupes, a.parts):
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


def _bundles(m, a):
    """ann bundle / edit-bundle / delete-bundle / bundles."""
    from . import metadocs
    an = annotations.Annotations.load(m)
    units = metadocs.Units(m, an)
    req = {"requested_by": a.requested_by} if getattr(a, "requested_by", None) else {}

    def parts_of(refs):
        out = []
        for r in refs:
            out += _targets(m, [r], parts=True, units=units)
        return out

    if a.cmd == "bundles":
        rows = list(an.bundles.values())
        if a.ref:
            b = an.bundles[an.find_bundle(a.ref)]
            key = annotations.target_key({"bundle": b["bundle"]})
            parts = []
            for p_ in b["parts"]:
                d = m.state.follow(p_["doc"])
                parts.append({"loc": locators.format(p_), "name": units.name(d), "date": units.date(d),
                              **({"text": p_["quote"]["exact"][:200]} if p_.get("quote") else {})})
            out({**units.describe("bundle:" + b["bundle"]), "from": b.get("from"),
                 "tags": [{"tag": t["tag"], "by": t["by"]} for t in an.tags.get(key, {}).values()],
                 "notes": [n for n in an.notes.values() if annotations.bundle_of(n["target"]) == b["bundle"]],
                 "facets": an.facets_of({"bundle": b["bundle"]}), "parts": parts})
            return
        if a.doc:
            doc = m.state.follow(locators.parse("doc:" + a.doc.removeprefix("doc:"), m.objects)["doc"])
            rows = an.bundles_of(doc)
        for b in rows:
            if not an.in_facet(a.facet, {"bundle": b["bundle"]}):
                continue
            key = annotations.target_key({"bundle": b["bundle"]})
            out({"bundle": b["bundle"], "name": b["name"], "parts": len(b["parts"]),
                 "documents": len({p_["doc"] for p_ in b["parts"]}), "by": b["by"],
                 **({k: b[k] for k in ("summary", "from") if b.get(k)}),
                 "tags": sorted(an.tags.get(key, {}))})
        return
    if a.cmd == "bundle":
        refs = list(a.parts)
        if a.list_file:
            _, items = lists.read(a.list_file)
            refs += [it["loc"] if isinstance(it["loc"], str) else locators.format(it["loc"]) for it in items]
        if not refs:
            raise ValueError("a bundle needs parts: locators, metadocuments (thread:D…) or --list FILE")
        r = {**req, "op": "define-bundle", "name": a.name, "parts": parts_of(refs), "summary": a.summary}
        if len(a.parts) == 1 and metadocs.is_ref(a.parts[0]) and not a.list_file:
            r["from"] = units.describe(a.parts[0])["ref"]
        res = annapi.request(m, {k: v for k, v in r.items() if v is not None})
        b = res["events"][0]["bundle"]
        for f in a.facet or []:
            res["events"] += annapi.request(m, {**req, "op": "file", "facet": f, "item": {"bundle": b}})["events"]
        out({"bundle": b, "name": a.name, "parts": len(r["parts"]), "events": res["events"]})
        return
    r = {**req, "op": a.cmd, "bundle": a.bundle}
    if a.cmd == "edit-bundle":
        b = an.bundles[an.find_bundle(a.bundle)]
        remove = []
        for x in a.remove:
            ts = parts_of([x])
            keys = {annotations.target_key(t) for t in ts}
            hit = [p_ for p_ in b["parts"] if annotations.target_key(p_) in keys
                   or (set(ts[0]) == {"doc"} and len(ts) == 1 and p_["doc"] == ts[0]["doc"])]
            if not hit:
                raise ValueError(f"{x} isn't a part of {b['name']!r}")
            remove += hit
        r.update(name=a.name, summary=a.summary, add_parts=parts_of(a.add) or None, remove_parts=remove or None)
    out(annapi.request(m, {k: v for k, v in r.items() if v is not None}))


def _people(m, a):
    """People from email headers and text who aren't entities (ann people, ann add-people)."""
    rows = entities.people(m)
    active = lambda p: p["sent"] + p["received"] + p.get("mentioned_in", 0)
    if a.cmd == "people":
        shown = [p for p in rows if a.all or (p["entity"] is None and p["looks_like"] == "person")]
        shown = [p for p in shown if max(p.get("mentioned_in", 0), p["sent"] + p["received"]) >= a.min_docs]
        for p in shown[:a.limit]:
            out({k: p[k] for k in ("name", "looks_like", "entity", "source", "addresses", "sent", "received", "first",
                                   "last", "roles", "mentioned_in", "missing_aliases") if p.get(k) not in (None, [], "")}
                | {"entity": p["entity"]})
        return
    req = {"requested_by": a.requested_by} if a.requested_by else {}
    wanted = []
    for n in a.names:
        f = " ".join(entities.fold_text(n).split())
        hits = [p for p in rows if f in {" ".join(entities.fold_text(x).split()) for x in [p["name"], *p.get("names", [])]}
                or n.lower() in {x.lower() for x in p.get("addresses", [])}]
        if not hits:
            raise KeyError(f"no one called {n!r} in `ann people --all`")
        wanted.append(hits[0])
    if a.top:
        wanted += sorted((p for p in rows if p["entity"] is None and p["looks_like"] == "person" and p not in wanted),
                         key=lambda p: -active(p))[:a.top]
    if a.known:
        wanted += [p for p in rows if p["entity"] and p["missing_aliases"] and p not in wanted]
    if not wanted:
        raise ValueError("name the people to add, or --top N, or --known (see `ann people`)")
    done = []
    for p in wanted:
        if p["entity"]:
            if p["missing_aliases"]:
                annapi.request(m, {**req, "op": "edit-entity", "entity": p["entity"]["entity"], "add_aliases": p["missing_aliases"]})
                done.append({"updated": p["entity"]["name"], "added_aliases": p["missing_aliases"]})
            continue
        kind = {"person": a.kind, "organization": "organization"}.get(p["looks_like"], "other")
        r = {**req, "op": "define-entity", "name": p["name"], "kind": kind,
             "aliases": p["missing_aliases"]}
        if (s := entities.person_summary(p)):
            r["summary"] = s
        ev = annapi.request(m, r)["events"][0]
        done.append({"added": p["name"], "entity": ev["entity"], "aliases": p["missing_aliases"],
                     **({"looks_like": p["looks_like"]} if p["looks_like"] != "person" else {})})
    for d in done:
        out(d)


def _link_row(an, ln: dict) -> dict:
    name = lambda i: an.entities[i]["name"] if i in an.entities else i
    row = {"link": ln["link"], "says": f"{name(ln['from'])} {ln['rel']} {name(ln['to'])}",
           "from": {"entity": ln["from"], "name": name(ln["from"])}, "rel": ln["rel"],
           "to": {"entity": ln["to"], "name": name(ln["to"])}}
    row.update({k: ln[k] for k in ("summary", "date", "see") if ln.get(k) not in (None, [])})
    if not ln.get("see"):
        row["unsupported"] = True
    facets = an.facets_of({"link": ln["link"]})
    if facets:
        row["facets"] = facets
    row["by"] = ln["by"]
    return row


def _graph_writes(m, a):
    """Links between entities, and facets (ann link, ann file, …)."""
    an = annotations.Annotations.load(m)
    req = {"requested_by": a.requested_by} if getattr(a, "requested_by", None) else {}
    ent = lambda ref: entities.resolve(m, ref, list(an.entities.values()))["entity"]

    def link_id(ref):
        if ref in an.links:
            return ref
        hits = [i for i in an.links if i.startswith(ref)] if len(ref) >= 6 else []
        if len(hits) == 1:
            return hits[0]
        raise KeyError(f"no link {ref!r}; ann links lists them")

    if a.cmd == "link":
        r = {**req, "op": "define-link", "from": ent(a.frm), "rel": a.rel, "to": ent(a.to),
             "summary": a.summary, "date": a.date}
        if a.see:
            r["see"] = _targets(m, a.see)
        res = annapi.request(m, {k: v for k, v in r.items() if v is not None})
        for f in a.facet or []:
            res["events"] += annapi.request(m, {**req, "op": "file", "facet": f,
                                                "item": {"link": res["events"][0]["link"]}})["events"]
        out(res)
    elif a.cmd == "edit-link":
        lid = link_id(a.link)
        r = {**req, "op": "edit-link", "link": lid, "rel": a.rel, "summary": a.summary, "date": a.date}
        if a.see:
            r["see"] = _targets(m, a.see)
        if a.add_see:
            r["see"] = (r.get("see") or an.links[lid].get("see") or []) + _targets(m, a.add_see)
        out(annapi.request(m, {k: v for k, v in r.items() if v is not None}))
    elif a.cmd == "delete-link":
        out(annapi.request(m, {**req, "op": "delete-link", "link": link_id(a.link)}))
    elif a.cmd == "links":
        e = ent(a.entity) if a.entity else None
        for ln in an.links.values():
            if e and e not in (ln["from"], ln["to"]):
                continue
            if a.rel and a.rel.lower() not in ln["rel"].lower():
                continue
            if not an.in_facet(a.facet, {"link": ln["link"]}):
                continue
            out(_link_row(an, ln))
    elif a.cmd in ("define-facet", "edit-facet"):
        r = {**req, "op": a.cmd, "facet": a.facet, "description": a.description, "color": a.color}
        out(annapi.request(m, {k: v for k, v in r.items() if v is not None}))
    elif a.cmd == "facets":
        for name, d in an.facets.items():
            counts = {}
            for k in an.filed.get(name, {}):
                kind = k.split(":", 1)[0]
                counts[kind] = counts.get(kind, 0) + 1
            out({**d, "filed": counts})
    elif a.cmd == "facet":
        if a.facet not in an.facets:
            raise KeyError(f"no facet {a.facet!r}" + (f"; facets: {', '.join(an.facets)}" if an.facets else ""))
        out(an.facets[a.facet])
        for f in an.filed.get(a.facet, {}).values():
            (kind, ref), = f["item"].items()
            row = {"item": kind, "filed_by": f["by"]}
            if kind == "entity":
                row.update(_entity_row(an.entities[ref]))
            elif kind == "link":
                row.update(_link_row(an, an.links[ref]))
            elif kind == "note":
                n = an.notes[ref]
                row.update({k: n[k] for k in ("note", "target", "body", "by", "at")})
            elif kind == "bundle":
                b = an.bundles[ref]
                row.update({"bundle": ref, "name": b["name"], "parts": len(b["parts"]), "by": b["by"]})
            else:
                row.update({"tag": ref, "applications": sum(1 for x in an.applications() if x["tag"] == ref)})
            out(row)
    else:
        items = [{"entity": ent(x)} for x in a.entity] + [{"link": link_id(x)} for x in a.link] \
            + [{"note": x} for x in a.note] + [{"bundle": x} for x in a.bundle] + [{"tag": x.strip()} for x in a.tags]
        if not items:
            raise ValueError(f"{a.cmd} needs --entity, --link, --note, --bundle or --tag")
        events = []
        for it in items:
            events += annapi.request(m, {**req, "op": a.cmd, "facet": a.facet, "item": it})["events"]
        out({"events": events})


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
        an = annotations.Annotations.load(m)
        row["links"] = [_link_row(an, ln) for ln in an.links.values() if e["entity"] in (ln["from"], ln["to"])]
        named = entities.find(" ".join(x for x in (e.get("summary"), e.get("recap")) if x), [x for x in ents if x is not e])
        if named:       # the entities its summary and recap name: where to go next
            row["names"] = [{"entity": i, "name": an.entities[i]["name"]} for i in dict.fromkeys(i for x in named for i in x["entities"])]
        facets = an.facets_of({"entity": e["entity"]})
        if facets:
            row["facets"] = facets
        out(row)
        if a.mentions:
            for x in ms:
                out({"loc": x["loc"]})
    elif a.cmd == "entities" and not a.doc:
        an = annotations.Annotations.load(m) if a.facet else None
        for e in ents:
            if (not a.kind or e["kind"] == a.kind) and (an is None or an.in_facet(a.facet, {"entity": e["entity"]})):
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
spreadsheet cells (`sheet=…&cells=B2:D10`, e.g. a grid selection), page
regions (`page=3&xywh=…`, e.g. an Alt-drag selection), values in JSON
(`ptr=/a/0/b`) or XML (`xpath=/a/b[2]`), which become text ranges, cells
by their column's header (`col=Amount&row=7`), an original's bytes (`bytes=0,16`,
from `raw hex`), or interpretations (`--interp NAME`; see `interp --help`).

A claim can carry what it was checked against: `page ref search page.md
nr '"never received" OR "not received"' --read all --against LOC --why "…"`
records the search, and :checked{#nr} shows it after the claim as one line
("checked: … 19 documents, read all 19: 1 against") that opens to every
document it found, the ones judged against first, each with its snippet.
The search re-runs whenever the page is checked or shown: if it finds other
documents now, the check is re-anchored (read the new ones, then `page
refs update`). Use it where the reader would ask "does anything say
otherwise?", and for things you say are not in the documents.

The documents behind a point, beyond the one or two the prose cites: `page
ref list page.md arrived lists/x.set.list.jsonl --point arrived`, shown
with :listed{#arrived} as a count that opens to each document and its why.

A reference can also be a metadocument (docs/METADOCS.md): `page ref add page.md
chase thread:<doc>` (or family:, copies:, versions:, new:, bundle:) cites
the whole thread, so the reader knows the scope is the conversation, not
one email. Use it with :cite{#chase} or [the thread](ref:chase); it can't
be quoted (quote a passage of one of its documents). If the thread gains a
reply or the bundle changes, `page check` reports it re-anchored, and
`page refs update` takes the new list.

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
    elif argv[i:i + 2] == ["ref", "search"]:
        argv[i:i + 2] = ["ref-search"]
    elif argv[i:i + 2] == ["ref", "list"]:
        argv[i:i + 2] = ["ref-list"]
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
    s.add_argument("--interp", metavar="NAME", help="a reference to an interpretation (interp --help), shown with :interp")
    s.add_argument("--ptr", help="with --interp: a JSON pointer into its output")
    s.add_argument("--replace", action="store_true")
    s = sub.add_parser("ref-search", help="page ref search <file> <id> \"<query>\": what a claim was checked against",
                       description="Record the search a claim was checked against, shown with :checked{#id}: "
                       "the query, its coverage, every document it finds (re-run whenever the page is "
                       "checked or shown, so new documents show up as re-anchored), how many you read, "
                       "and the passages you judged to go against the claim (--against, with --why).")
    s.add_argument("file")
    s.add_argument("id")
    s.add_argument("query", help="a keyword query (search --help); it must be exhaustive, so not semantic")
    s.add_argument("--where", help="as search --where (e.g. custodian:smith-j)")
    s.add_argument("--against", action="append", default=[], metavar="LOC",
                   help="a passage that goes against the claim (repeat; pair each with --why)")
    s.add_argument("--why", action="append", default=[], help="why the --against passage at the same position does")
    s.add_argument("--read", help="how many of its documents you read for contrary statements: all, or a number")
    s.add_argument("--note", help="a line on what the check shows, e.g. what the search was meant to catch")
    s.add_argument("--replace", action="store_true")
    s = sub.add_parser("ref-list", help="page ref list <file> <id> <list file> [--point P]: the documents behind a point",
                       description="Reference a list file (a key set, a chronology), or one point of a key set, "
                       "shown with :listed{#id}: a count that opens to its documents, each with its why.")
    s.add_argument("file")
    s.add_argument("id")
    s.add_argument("list_file")
    s.add_argument("--point", help="only the items supporting this key-set point")
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
        elif a.cmd == "ref-add" and a.interp:
            if a.locators or a.words:
                raise pages.PageError("--interp makes a reference to an interpretation; don't give locators or --words too")
            out(pages.add_interp_ref(m, a.file, a.id, a.interp, a.ptr, a.replace))
        elif a.cmd == "ref-add":
            if a.ptr:
                raise pages.PageError("--ptr goes with --interp; for a JSON document, use a ptr= locator (doc:…#ptr=/a/b)")
            if bool(a.locators) == bool(a.words):
                raise pages.PageError("give locators, or --words (with --in), not both")
            locs = a.locators or pages.find_words(m, a.in_doc, a.words, a.layer)
            out(pages.add_ref(m, a.file, a.id, locs, a.replace))
        elif a.cmd == "ref-list":
            out(pages.add_list_ref(m, a.file, a.id, a.list_file, a.point, a.replace))
        elif a.cmd == "ref-search":
            if len(a.why) > len(a.against):
                raise pages.PageError("each --why goes with the --against before it")
            read = None if a.read is None else "all" if a.read == "all" else int(a.read)
            out(pages.add_search_ref(m, a.file, a.id, a.query, a.where,
                                     list(zip(a.against, a.why + [None] * (len(a.against) - len(a.why)))),
                                     read, a.note, a.replace))
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

Keys in the viewer: j/k move, Enter open, ]/[ next/previous in the
pane's sequence (or, with none, its highlights), Space page, m mark, t tag palette (Shift+Enter tags the family, Ctrl+Enter
the duplicates), N note, e entity (add the selection, or edit the entity it
is), E show entities (all, known, none), tag hotkeys, v layer, f mode, Tab pane,
H overview, Alt-drag a region on a page, ? help. Spreadsheets open as a grid: click, drag, or
Shift+arrows select cells, and row and column headers select whole rows
and columns. The selection is a `sheet=…&cells=B2:D10` locator.
Hex mode (the pane's ⋯ menu, or opening a bytes= locator) shows the
original's bytes; a drag selects a `bytes=a,b` locator. The same menu
saves the document to the user's Downloads folder (original, PDF, PDF
with annotations, text); that is the user's to do, not Claude's.

Highlights: a document pane's highlights get a bar with Prev/Next
buttons, the current one's label, and a side-panel list of all of them by
label, so give each a short --label saying why it matters. The user steps
through them in document order (the buttons, or ]/[ when the pane has no
sequence); `view state` marks the one they're on "current". To show
several places in one document, highlight each one: no sequence needed.

Entities: the viewer underlines known entities and dots suggested ones;
the user hovers to see what they are and clicks to select one. `view
selection` then has "entity": {"known": [...]} or {"suggested": {...}}.
See `ann --help` for the entity list.

Timelines: `view timeline FILE` shows a list file's items by date, in
lanes (--lanes group, custodian, from, kind). For a chronology, give items
their own fields "date" (YYYY-MM-DD, YYYY-MM or YYYY) and "event" (the
label), each with a locator to its evidence. `view timeline --entity
"Rita Ames"` shows an entity's dated links, each at its evidence, in a
lane per entity at the other end (--by rel for one per relation);
undated links are listed after (--doc-dates places them at their
evidence's date, marked approximate), and links with no evidence are
left out and reported. `graph timeline` writes the same as a list file.

Issues: the user picks a facet (an issue: `ann --help`) with F or the ◇
button, and what isn't filed under it is dimmed; `view state` and `view
selection` report it as "facet". Read it as the issue they're working on.
`view facet NAME` (or `--clear`) switches it for them.

Diagrams: `view graph` draws the entity list's links as boxes and arrows
(`view graph "Rita Ames" --depth 2`, `--docs` for the documents they rest
on, `--facts` to make each link a box documents point at, `--facet F` for
one issue, `--cooccur` for names found together, dashed). A click on a
box or an arrow's label previews its evidence. See `graph --help`.

Metadocuments: `view meta thread:D` (or family:, copies:, versions:,
new:, bundle:) shows its parts in a list pane with a linked preview: a
thread in reply order with copies under each message, a family from the
top down, a bundle's parts as recorded. `view meta D --of` shows every
metadocument a document is in, a group each; `view meta D --splits`
shows its recorded splits (meta split --record), a row per piece, to step
through with the preview. --list FILE keeps it as a list file.

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

Files: the user can hand you files that aren't discovery documents (a
draft to mark up, a client's notes, a template) by dropping them on the
viewer or its terminal; they land in /matter/workspace/files/uploads/
(dropped on the terminal, their paths are typed at the prompt). They are
plain files: no document IDs, not searchable with `search`, never quoted as
evidence. Read, change or delete them as the task needs. `view files` shows
the user the Files pane (both folders). To hand the user a file you made
(a .docx, a spreadsheet, a PDF), `view deliver FILE --note "what it is"`:
it is copied to /matter/workspace/files/from-claude/ and shown in the Files
pane, where the user saves it to their Downloads folder.

Services: a pane's <form action="svc:NAME"> is answered immediately by
`view serve NAME -- cmd …` (run it in the background), without waiting for
the next prompt. cmd gets {"form", "selection", "pane"} as JSON on stdin and
prints the pane's new HTML.
"""


def meta_list(m, ref, of=False, splits=None):
    """(header, items) for `view meta` and the viewer's own "list parts"
    links (adhoc meta-pane): a metadocument's parts, everything a document
    is part of (``of``), or its recorded splits (``splits``: "" for all, or
    a split's name)."""
    from . import metadocs
    ann = annotations.Annotations.load(m)
    if of or splits is not None:
        if metadocs.is_ref(ref):
            raise ValueError("--of and --splits take a document, not a metadocument")
        doc = m.state.follow(locators.parse("doc:" + ref.removeprefix("doc:"), m.objects)["doc"])
        if of:
            return metadocs.around_list(m, doc, ann)
        from . import split
        return split.splits_list(ann, doc, metadocs.Units(m, ann).name(doc), splits or None)
    if not metadocs.is_ref(ref):
        raise ValueError(f"{ref} is a document: name a metadocument ({', '.join(metadocs.PREFIXES)}), "
                         "or use --of (everything it is in) or --splits")
    return metadocs.list_of(m, ref, ann)


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
    s = sub.add_parser("highlight", help="mark a place for the user, labelled; stays until cleared. "
                                         "The user steps through a pane's highlights in order")
    s.add_argument("loc")
    s.add_argument("--label", help="shown to the user: what this passage is and why it matters")
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
    s = sub.add_parser("meta", help="show a metadocument's parts (thread, family, copies, versions, new, bundle) "
                                    "in a list pane, with a linked preview pane")
    s.add_argument("ref", help="thread:D, family:D, copies:D, versions:D, new:D, bundle:B; or a document with --of or --splits")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--of", action="store_true", help="REF is a document: every metadocument it is in, a group each")
    g.add_argument("--splits", nargs="?", const="", metavar="NAME",
                   help="REF is a document: its recorded splits, a group each, a row per piece (or only split NAME)")
    s.add_argument("--list", dest="list_file", help="also write it as a list file")
    s.add_argument("--pane", help="replace this list pane's content")
    s.add_argument("--preview", metavar="DOC_PANE", help="a document pane to follow the selection, in any window")
    where(s)
    s = sub.add_parser("timeline", help="show a list file's items by date, in lanes, with a linked preview pane")
    s.add_argument("file", nargs="?", help="a list file; items' fields date and event make it a chronology")
    s.add_argument("--entity", action="append", metavar="REF",
                   help="instead of a file: these entities' dated links, each at its evidence (repeatable; "
                        "graph timeline writes the same as a list file)")
    s.add_argument("--lanes", choices=["auto", "group", "custodian", "from", "kind", "none"], default="auto",
                   help="auto: the list's groups if it has them, else custodian")
    _timeline_opts(s)
    s.add_argument("--scale", choices=["auto", "day", "week", "month", "year"], default="auto")
    s.add_argument("--title")
    s.add_argument("--pane", help="replace this timeline pane's content")
    s.add_argument("--preview", metavar="DOC_PANE", help="a document pane to follow the selection, in any window")
    where(s)
    s = sub.add_parser("graph", help="draw entities, their links and their evidence as a diagram, with a linked preview pane")
    s.add_argument("refs", nargs="*", help="center on these entities or documents (default: every entity with a link)")
    s.add_argument("--depth", type=int, default=1, help="with refs: how many hops out")
    s.add_argument("--layout", choices=["auto", "kind", "depth"], default="auto",
                   help="kind: organizations, people, then events, contracts and the rest, then documents, in columns; "
                        "depth: columns by hops from the refs (auto: depth with refs, else kind)")
    s.add_argument("--docs", action="store_true", help="show the documents links and entities rest on")
    s.add_argument("--all", action="store_true", help="also entities with no link")
    s.add_argument("--title")
    s.add_argument("--pane", help="replace this graph pane's content")
    s.add_argument("--preview", metavar="DOC_PANE", help="a document pane to follow the selection, in any window")
    _graph_opts(s)
    where(s)
    s = sub.add_parser("trace", help="draw a fact's trace (analyze trace --list) as a time DAG: events joined by "
                                     "how each came from the last, unrelated strands apart (docs/TRACE.md)")
    s.add_argument("file", help="a list file written by analyze trace --list")
    s.add_argument("--title")
    s.add_argument("--pane", help="replace this pane's content")
    s.add_argument("--preview", metavar="DOC_PANE", help="a document pane to follow the selection, in any window")
    s = sub.add_parser("dispute", help="draw a dispute (analyze dispute --list) as a time DAG: events colored by "
                                       "the sides their speakers take, switches and sides kept despite a colleague "
                                       "in red (docs/DISPUTES.md)")
    s.add_argument("file", help="a list file written by analyze dispute --list")
    s.add_argument("--title")
    s.add_argument("--pane", help="replace this pane's content")
    s.add_argument("--preview", metavar="DOC_PANE", help="a document pane to follow the selection, in any window")
    s = sub.add_parser("facet", help="show the user one issue (a facet: ann --help), dimming what isn't filed under it")
    s.add_argument("facet", nargs="?", help="omit, or --clear, for every issue")
    s.add_argument("--clear", action="store_true")
    s = sub.add_parser("close")
    s.add_argument("pane")
    s = sub.add_parser("home", help="show the overview: what the matter holds (the first window opens on it)")
    s.add_argument("--window", help="default: the window the user last used")
    s = sub.add_parser("files", help="show the Files pane: what the user handed you, and what you handed them")
    s.add_argument("--window", help="default: the window the user last used")
    s = sub.add_parser("deliver", help="hand the user files (not discovery documents): copied to "
                                       "files/from-claude/ and shown in the Files pane, to save")
    s.add_argument("paths", nargs="+", metavar="FILE")
    s.add_argument("--note", help="shown beside them: what they are")
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
        from .received import NAMES, resolve_name
        from . import metadocs
        if metadocs.is_ref(x):                # a metadocument opens at its first part
            return locators.format(locators.expand(m, metadocs.members(m, x)[0]))
        if x.startswith(NAMES):               # the first document a Bates range, exhibit or control number names
            return locators.format(resolve_name(m, x)[0]["loc"])
        parsed = locators.parse(x if x.lstrip().startswith("{") else "doc:" + x.removeprefix("doc:"), m.objects)
        # the viewer reads canonical selectors: aliases (ptr, xpath, col, pl, label) become what they name
        return locators.format(locators.expand(m, parsed))

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
    elif a.cmd == "trace":
        from . import trace as tracemod
        header, _ = lists.read(a.file)
        result, _ = tracemod.again(m, header)
        title = a.title or header.get("title") or "Trace"
        files, sequence, summary = tracemod.dag_html(m, result, title)
        c = {"cmd": "pane", "kind": "graph", "files": files, "sequence": sequence, "title": title, "update": a.pane,
             "link": a.preview, "preview_new": not a.preview and not a.pane}
        shown = summary
    elif a.cmd == "dispute":
        from . import dispute as dispmod
        header, _ = lists.read(a.file)
        result, _ = dispmod.again(m, header)
        title = a.title or header.get("title") or "Dispute"
        files, sequence, summary = dispmod.dag_html(m, result, title)
        c = {"cmd": "pane", "kind": "graph", "files": files, "sequence": sequence, "title": title, "update": a.pane,
             "link": a.preview, "preview_new": not a.preview and not a.pane}
        shown = summary
    elif a.cmd == "meta":
        header, items = meta_list(m, a.ref, a.of, a.splits)
        if a.list_file:
            lists.write(a.list_file, header, items)
        files, sequence = viewmod.list_html(m, (header, items))
        c = {"cmd": "pane", "kind": "list", "files": files, "sequence": sequence, "title": header["title"],
             "update": a.pane, "link": a.preview, "preview_new": not a.preview and not a.pane}
        shown = {"title": header["title"], "items": len(items), "rows": len(sequence),
                 **({"list": a.list_file} if a.list_file else {})}
    elif a.cmd == "timeline":
        from .timeline import timeline_html
        if bool(a.file) == bool(a.entity):
            raise ValueError("view timeline takes a list file or --entity, not both")
        if a.entity:
            from . import graph as graphmod
            header, items, links_summary = graphmod.timeline_list(m, a.entity, a.facet, a.by, a.doc_dates)
            source = (header, items)
        else:
            source = Path(a.file)
            header, _ = lists.read(a.file)
        files, sequence, summary = timeline_html(m, source, a.lanes, a.scale, a.title)
        c = {"cmd": "pane", "kind": "timeline", "files": files, "sequence": sequence,
             "title": a.title or header.get("title") or Path(a.file).stem, "update": a.pane,
             "link": a.preview, "preview_new": not a.preview and not a.pane}
    elif a.cmd == "graph":
        from . import graph as graphmod
        ann = annotations.Annotations.load(m)
        g, ids = _load_graph(m, a, ann, a.refs)
        if a.refs:
            g = g.around(ids, a.depth)
        if not (a.docs or a.doc_links):
            g = g.subgraph([i for i, n in g.nodes.items() if n["type"] != "doc" or i in ids])
        if not a.all and not a.refs:
            joined = {x for e in g.edges for x in (e["from"], e["to"])}
            g = g.subgraph([i for i in g.nodes if i in joined])
        layout = a.layout if a.layout != "auto" else ("depth" if a.refs else "kind")
        title = a.title or ("Around " + ", ".join(a.refs) if a.refs else "Relationships") + (f" · {a.facet}" if a.facet else "")
        files, sequence, summary = graphmod.diagram_html(m, g, title, layout)
        c = {"cmd": "pane", "kind": "graph", "files": files, "sequence": sequence, "title": title, "update": a.pane,
             "link": a.preview, "preview_new": not a.preview and not a.pane}
    elif a.cmd == "facet":
        c["facet"] = None if a.clear else a.facet
    elif a.cmd in ("close",):
        c["pane"] = a.pane
    elif a.cmd == "files":
        c["window"] = a.window
    elif a.cmd == "deliver":
        given = viewmod.deliver(m, a.paths, a.note)
        c = {"cmd": "files", "show": "from-claude", "names": [x["name"] for x in given], "window": a.window}
        try:
            r = viewmod.command(m, {k: v for k, v in c.items() if v is not None})
        except viewmod.NotRunning:
            r = {"note": "the viewer isn't running: the user finds them in the Files pane when it is"}
        out({"delivered": given, **r})
        return
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
    if a.cmd in ("open", "sequence", "pane", "list", "timeline", "graph", "meta"):
        c.update(window=a.window, new_window=a.new_window)
    result = viewmod.command(m, {k: v for k, v in c.items() if v is not None and v is not False})
    if a.cmd == "timeline":
        result["timeline"] = {k: summary[k] for k in ("items", "dated", "undated", "lanes", "segments", "truncated")}
        if a.entity and links_summary["no_evidence"]:
            result["timeline"]["no_evidence"] = links_summary["no_evidence"]     # not shown: nothing to point at
    if a.cmd == "graph":
        result["graph"] = summary
    if a.cmd in ("trace", "dispute"):
        result[a.cmd] = shown
    if a.cmd == "meta":
        result["meta"] = shown
    out(result)


# -- graph ---------------------------------------------------------------------------------

GRAPH_HELP = """\
The matter as a graph: entities (people, organizations, contracts, events…),
the links between them (`ann link`), and the documents they rest on.

  graph show REF                     a node and everything joined to it, each with its evidence
  graph around REF [--depth 2]       the neighborhood of an entity or document
  graph path A B [--max 4]           how two things connect, hop by hop, with the evidence for each
  graph evidence REF                 for a document: the links and entities that rest on it;
                                     for an entity or link: the documents it rests on
  graph page REF [-o FILE]           a literate page about one entity: its connections, each with its
                                     evidence quoted by reference, in date order, and where it is named
  graph timeline REF… [-o FILE]      a list file of an entity's dated links, each at its evidence,
                                     for `view timeline FILE` (or `view timeline --entity REF`)
  graph unsupported [--facet F]      links and events with no evidence recorded (--all: any entity)
  graph export [--format json|dot|mermaid] [-o FILE]
                                     the whole graph (or --around REF), for your own code

REF is an entity (ID, name or alias), a link ID (l_…), or a document.
Documents relate too: --doc-links adds replies (from the headers),
quotations within a thread, versions (near-duplicate texts, or one mostly
inside another) and copies, for the documents in the graph (a document you
name is in it), --doc-links 2 for two steps out. `graph show DOC
--doc-links` lists a document's relations; `graph path DOC1 DOC2
--doc-links 3` finds how two documents connect. Versions and quotations are
estimates (marked derived, with their similarity); replies are facts of
the headers.

--email adds who emailed whom, from the headers: `emailed` between people
(with the count and the emails) and `sent`/`received` to each email. It
needs the people's addresses among their aliases: `ann people` lists people
from the headers and the text who aren't entities, and `ann add-people`
adds them with their addresses.

--grain thread|family|copies|versions|bundle gathers documents into
metadocuments (docs/METADOCS.md): a thread is one node with the facts that rest
on any of its messages, a bundle one node for its documents.

Options shared by all: --mentions adds every document an entity is named in;
--cooccur adds entities named together in --min-cooccur documents or more
(default 2); --facts makes each link a node, so documents point at the facts
they support; --facet F keeps only what is filed under the facet.

Recorded edges (links and their evidence, entities' --see) are someone's
judgment, with an author. `mentions` and `co-mention` edges are found by
matching names in text, are marked "derived", and are leads: never present
a co-mention as a relationship.

From Python: `from adhocdocs import graph; g = graph.load(Matter.find(None),
mentions=True)` gives `g.nodes` and `g.edges` as plain dicts, with
`g.around`, `g.paths` and `g.subgraph`. `graph export` writes the same JSON.

To show it: `view graph` draws a diagram in a viewer pane (see its --help).
"""


def _graph_opts(s):
    s.add_argument("--mentions", action="store_true", help="add the documents each entity is named in")
    s.add_argument("--cooccur", action="store_true", help="add entities named together (derived: a lead, not a link)")
    s.add_argument("--min-cooccur", type=int, default=2)
    s.add_argument("--facts", action="store_true", help="each link a node, joined to its evidence")
    s.add_argument("--facet", help="only what is filed under this facet")
    s.add_argument("--doc-links", type=int, nargs="?", const=1, default=0, metavar="HOPS",
                   help="add how documents relate (replies, quotations, versions, copies, attachments), "
                        "HOPS steps out from the documents already in the graph (default 1)")
    s.add_argument("--email", action="store_true",
                   help="add who emailed whom, from the headers, for people whose aliases hold their addresses")
    s.add_argument("--grain", type=_grain_arg(("doc", "family", "thread", "copies", "versions", "bundle")), default="doc",
                   help="gather documents into metadocuments: one node per family, thread, item, text or bundle")


def _timeline_opts(s):
    s.add_argument("--facet", help="only links filed under this facet (or between entities filed under it)")
    s.add_argument("--by", choices=["other", "rel", "none"], default="other",
                   help="lanes: the entity at the other end of each link (default), the relation, or none")
    s.add_argument("--doc-dates", action="store_true",
                   help="place undated links at their evidence's date, marked approximate (default: listed as undated)")


def _load_graph(m, a, ann=None, refs=()):
    """The graph, and the node IDs ``refs`` name. Documents named are in it
    before document relations are added, so theirs are."""
    from . import graph as graphmod
    g = graphmod.load(m, mentions=a.mentions, cooccur=a.cooccur, facts=a.facts, facet=a.facet,
                      min_cooccur=a.min_cooccur, ann=ann)
    if a.email:
        graphmod.add_correspondence(m, g)
    ids = [graphmod.resolve(m, g, r, ann) for r in refs]
    if a.doc_links:
        graphmod.add_doc_links(m, g, a.doc_links)
    if getattr(a, "grain", "doc") != "doc":
        g = graphmod.regrain(m, g, a.grain, ann)
        ids = [i if i in g.nodes else next(u for u, n in g.nodes.items() if i in n.get("members", ())) for i in ids]
    return g, ids


@run
def graph(argv=None):
    from . import graph as graphmod
    p = parser("graph", GRAPH_HELP)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("show", help="a node and its edges, each with its evidence")
    s.add_argument("ref")
    _graph_opts(s)
    s = sub.add_parser("around", help="the nodes within --depth hops, and the edges among them")
    s.add_argument("refs", nargs="+")
    s.add_argument("--depth", type=int, default=1)
    _graph_opts(s)
    s = sub.add_parser("path", help="the shortest ways A and B connect")
    s.add_argument("a")
    s.add_argument("b")
    s.add_argument("--max", type=int, default=4, help="at most this many hops")
    s.add_argument("--limit", type=int, default=5)
    s.add_argument("--through-docs", action="store_true",
                   help="allow hops through documents (two entities shown by the same document)")
    _graph_opts(s)
    s = sub.add_parser("evidence", help="what rests on a document, or what an entity or link rests on")
    s.add_argument("ref")
    s = sub.add_parser("timeline", help="a list file of entities' dated links, each at its evidence, for view timeline")
    s.add_argument("refs", nargs="+", help="entities (ID, name or alias)")
    _timeline_opts(s)
    s.add_argument("-o", "--out", help="default: timeline-<name>.list.jsonl")
    s = sub.add_parser("unsupported", help="links and events with no evidence recorded")
    s.add_argument("--facet")
    s.add_argument("--all", action="store_true", help="every entity without --see, not only events")
    s = sub.add_parser("page", help="a literate page about one entity: its links and their evidence, quoted by reference")
    s.add_argument("ref")
    s.add_argument("-o", "--out", help="default: entity-<name>.md")
    s.add_argument("--mentions", type=int, default=15, help="list this many of the documents that name it")
    s = sub.add_parser("export", help="the graph as JSON (nodes, edges), Graphviz dot, or Mermaid")
    s.add_argument("--format", choices=["json", "dot", "mermaid"], default="json")
    s.add_argument("--around", action="append", help="only around these (repeatable)")
    s.add_argument("--depth", type=int, default=1)
    s.add_argument("-o", "--out")
    _graph_opts(s)
    matter_everywhere(sub)
    a = p.parse_args(argv)
    m = Matter.find(a.matter)
    ann = annotations.Annotations.load(m)

    if a.cmd == "unsupported":
        for row in graphmod.unsupported(ann, a.facet, a.all):
            out(row)
        return
    if a.cmd == "timeline":
        import re as _re
        header, items, summary = graphmod.timeline_list(m, a.refs, a.facet, a.by, a.doc_dates, ann)
        name = summary["entities"][0]["name"]
        path = Path(a.out or "timeline-" + (_re.sub(r"[^a-z0-9]+", "-", entities.fold_text(name)).strip("-") or "entity")
                    + ".list.jsonl")
        lists.write(path, header, items)
        out({"written": str(path), **summary, "next": f"view timeline {path}"})
        return
    if a.cmd == "page":
        import re as _re
        text = graphmod.dossier(m, a.ref, a.mentions)
        name = text.split("\n", 1)[0].removeprefix("# ")
        path = Path(a.out or "entity-" + (_re.sub(r"[^a-z0-9]+", "-", entities.fold_text(name)).strip("-") or "page") + ".md")
        path.write_text(text, encoding="utf-8")
        from . import literate
        problems = literate.problems(literate.check(m, text))
        out({"written": str(path), "problems": problems, "next": f"page view {path}"})
        return
    if a.cmd == "evidence":
        g = graphmod.load(m, facts=True, ann=ann)
        nid = graphmod.resolve(m, g, a.ref, ann)
        node = g.nodes[nid]
        if node["type"] == "doc":
            doc = node["doc"]
            out({"doc": doc, "label": node["label"]})
            for ln in ann.links.values():
                hits = [t for t in ln.get("see") or [] if m.state.follow(t["doc"]) == doc]
                if hits:
                    out({**_link_row(ann, ln), "here": hits})
            for e in ann.entities.values():
                hits = [t for t in e.get("see") or [] if m.state.follow(t["doc"]) == doc]
                if hits:
                    out({"entity": e["entity"], "name": e["name"], "kind": e["kind"], "here": hits})
            return
        if node["type"] == "entity":
            e = ann.entities[node["entity"]]
            out({"entity": e["entity"], "name": e["name"], "see": e.get("see") or []})
            for ln in ann.links.values():
                if e["entity"] in (ln["from"], ln["to"]):
                    out(_link_row(ann, ln))
            return
        out(_link_row(ann, ann.links[node["link"]]))
        return

    refs = {"show": lambda: [a.ref], "around": lambda: a.refs, "path": lambda: [a.a, a.b],
            "export": lambda: a.around or []}[a.cmd]()
    g, ids = _load_graph(m, a, ann, refs)
    if a.cmd == "show":
        nid = ids[0]
        out(g.nodes[nid])
        for e in g.incident(nid):
            o = g.other(e, nid)
            row = {"edge": e["kind"], "direction": "out" if e["from"] == nid else "in",
                   "other": {k: g.nodes[o][k] for k in ("id", "label", "type") if k in g.nodes[o]},
                   "says": graphmod.describe_step(g, {"from": nid, "edge": e, "to": o})["says"]}
            row.update({k: e[k] for k in ("link", "rel", "date", "see", "loc", "count", "docs", "derived", "summary",
                                          "similarity", "containment", "field") if e.get(k)})
            out(row)
    elif a.cmd == "around":
        sub_g = g.around(ids, a.depth)
        out(sub_g.to_json())
    elif a.cmd == "path":
        x, y = ids
        through = a.through_docs or bool(a.doc_links) or x.startswith("doc:") or y.startswith("doc:")
        paths = g.paths(x, y, a.max, limit=a.limit, avoid_docs=not through)
        if not paths:
            out({"paths": 0, "note": f"no connection within {a.max} hops"
                 + ("" if through else "; --through-docs also walks through shared documents")
                 + ("" if a.mentions or a.cooccur else "; --mentions or --cooccur add connections found in the text")})
        for path in paths:
            out({"hops": len(path), "steps": [graphmod.describe_step(g, st) for st in path]})
    elif a.cmd == "export":
        if a.around:
            g = g.around(ids, a.depth)
        text = {"json": lambda: json.dumps(g.to_json(), ensure_ascii=False, indent=1) + "\n",
                "dot": g.to_dot, "mermaid": g.to_mermaid}[a.format]()
        if a.out:
            Path(a.out).write_text(text, encoding="utf-8")
            out({"written": a.out, "nodes": len(g.nodes), "edges": len(g.edges)})
        else:
            sys.stdout.write(text)


# -- meta ------------------------------------------------------------------------------------

META_HELP = """\
Metadocuments (docs/METADOCS.md): content at another scale than the documents
ingestion made. Documents are the atoms; a metadocument is any other
boundary: a family, a thread, every copy of an item, every draft of a text,
what one message adds without what it quotes, or a bundle someone recorded
(ann bundle). They aren't listed or indexed; each is named from any
document in it:

  family:D     D with its parent and attachments
  thread:D     the email thread D is in, with copies
  copies:D     D however many copies: duplicates, same text, same message
  versions:D   every draft, revision and copy of D's text (estimated)
  new:D        what message D adds: its text without the quoted part
  bundle:B     a recorded bundle, by ID, ID prefix or name
  thread+family:D   grains joined by +: everything reachable through any
               of them (a thread with all its attachments; add +copies for
               every copy). Any of thread, family, copies, versions

  meta grains                       the grains and what each means
  meta show REF [--list FILE]       what REF names: its documents and parts
  meta of DOC                       every metadocument a document is in
  meta split DOC                    where a merged PDF's letters, memos and forms may begin
  meta split --find                 the documents that look like several
  meta splits DOC                   the splits recorded for a document

`view meta REF` shows any of these in the viewer, parts in a list pane
with a preview; `view meta DOC --of` everything a document is in, and
`view meta DOC --splits` its recorded splits, a row per piece.

Splitting (split.py). A scan of a stack of papers, or PDFs merged into
one, is one document to the store and several to a reader. Where the
pieces are is a judgment (a letter with its enclosures is one thing or
three; a fax cover sheet goes with what it covers, or not), so the
detector advises and you decide, and the user's word beats both. A split
someone keeps is recorded as bundles, one per piece.

The detector weighs each page after the first: printed page numbers
starting again or carrying on; openings (letterhead, Dear…, a memo's
TO/FROM, an email's From/Sent/To, a caption, a form's title) and closings
on the page before; sentences running on; running headers and footers;
and whether the page looks like the ones before it, in its words (the
rarer words and reference numbers they share), its layout (margins,
type size, line spacing) and its image (page size, paper colour, a
letterhead's art at the top, from small pictures of the pages). A page that looks like neither can start a
piece with nothing to announce it. Each candidate has a strength (0–1)
and its reasons for and against. --level sure, likely (the default) or
possible gives a split, coarse to fine.

Judging it yourself:
  meta split DOC                     the candidates and the likely split
  meta split DOC --pages 5-9         each page's printed number, header and footer,
                                     first and last lines, how like the page before it is,
                                     and the case for a cut there; `text DOC#page=7` reads one
  meta split DOC --sheet F.png       pictures of the pages, numbered, in a grid (--pages to
                                     limit): letterhead art, paper, handwriting, stamps
  meta split DOC --cut 12 --join 7   the likely split, adjusted; --at 3,6,9 cuts exactly there
  meta split DOC --plan FILE|-       your own split: one JSON object a piece, in order,
                                     {"pages": "1-2", "label": "…", "kind": "…", "summary": "…"};
                                     "pages" can be several ranges ("1-2,7": a misfiled page),
                                     pages in no piece (slip sheets) are reported, overlaps refused
Look where the evidence is mixed or thin, where the user cares (they named
the letters they want), or whenever a cut would change a review call.

Keeping it: --list FILE writes the pieces as a list of page ranges (`view
list FILE` steps through them with a preview, for the user to check).
--record NAME keeps the split as bundles named "DOC · NAME k/n · label"
(tag or note a piece as one; `ann bundles`); a document can have several
splits under different names ("letters", "with-enclosures"), --replace
re-records one, and the user's can't be replaced. In code: `from adhocdocs
import split` (pages, boundaries, similarities, pieces, from_plan).

A metadocument goes anywhere a locator does: `text thread:D` prints each
part, `ann tag responsive thread:D` tags each message, `view open new:D`
opens its first part. `search … --grain G` and `graph … --grain G` report
metadocuments instead of documents.
"""


@run
def meta(argv=None):
    from . import metadocs
    p = parser("meta", META_HELP)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("grains")
    s = sub.add_parser("show", help="the documents and parts a metadocument names")
    s.add_argument("ref")
    s.add_argument("--list", dest="list_file", help="also write its parts as a list file (view list FILE shows it)")
    s = sub.add_parser("of", help="the metadocuments a document is in")
    s.add_argument("doc")
    s = sub.add_parser("split", help="where a merged PDF's pieces may begin: candidates, and a split to keep as bundles")
    s.add_argument("doc", nargs="?")
    s.add_argument("--find", action="store_true", help="instead: the documents that look like several, at --level")
    s.add_argument("--level", choices=list(LEVELS_), default="likely",
                   help="how sure a candidate must be to cut there: sure, likely (default) or possible")
    s.add_argument("--at", help="cut exactly here: the pages pieces start at, e.g. 3,6,9 (page 1 is implied)")
    s.add_argument("--cut", type=int, action="append", default=[], help="also start a piece at this page")
    s.add_argument("--join", type=int, action="append", default=[], help="don't start a piece at this page")
    s.add_argument("--plan", help="your own split instead: a file (or - for stdin) of pieces, one JSON object each: "
                                   '{"pages": "1-2,7", "label": …, "kind": …, "summary": …}')
    s.add_argument("--pages", nargs="?", const="all", metavar="RANGE",
                   help="instead: what each page shows at its edges and how it compares with the page before "
                        "(all pages, or e.g. 5-9)")
    s.add_argument("--sheet", metavar="FILE.png", help="instead: pictures of the pages in a numbered grid (with --pages: those)")
    s.add_argument("--list", dest="list_file", help="also write the pieces as a list file of page ranges")
    s.add_argument("--record", metavar="NAME", nargs="?", const="split",
                   help="keep the split as bundles, one per piece, under this name (default: split)")
    s.add_argument("--replace", action="store_true", help="with --record: replace a split of that name")
    s.add_argument("--requested-by")
    s = sub.add_parser("splits", help="the splits recorded for a document, each with its pieces")
    s.add_argument("doc")
    matter_everywhere(sub)
    a = p.parse_args(argv)
    if a.cmd == "grains":
        for g, what in metadocs.GRAINS.items():
            out({"grain": g, "means": what})
        out({"grain": "bundle", "means": "a set of documents or parts of them someone recorded as one (ann bundle)"})
        out({"grain": "A+B", "means": f"grains joined (any of {', '.join(metadocs.COMPOSABLE)}): everything reachable "
                                      "through any of them, step after step. thread+family is a thread with all its "
                                      "attachments; thread+family+copies adds every copy"})
        return
    m = Matter.find(a.matter)
    if a.cmd in ("split", "splits"):
        _split(m, a)
        return
    u = metadocs.Units(m)
    if a.cmd == "of":
        for row in u.units_of(a.doc):
            out(row)
        return
    ref = a.ref if metadocs.is_ref(a.ref) else "doc:" + a.ref.removeprefix("doc:")
    if ref.startswith("doc:"):
        raise ValueError(f"{a.ref} is a document; name a metadocument ({', '.join(metadocs.PREFIXES)})")
    d = u.describe(ref)
    out(d)
    parts = u.members(d["ref"])
    rows = []
    for part in parts:
        doc = part["doc"]
        row = {"loc": locators.format(part), "name": u.name(doc), "date": u.date(doc)}
        if part.get("quote"):
            row["text"] = part["quote"]["exact"][:200]
        rows.append(row)
        out(row)
    if a.list_file:
        lists.write(a.list_file, *lists.new(d["name"], [r["loc"] for r in rows], query=d["ref"]))


LEVELS_ = ("sure", "likely", "possible")


def _split(m, a):
    """meta split / meta splits."""
    from . import split
    from .analysis import _ready
    rows = _ready(m, "fields").rows()
    if a.cmd == "split" and a.find:
        for r in split.find(m, a.level):
            out(r)
        return
    if not a.doc:
        raise ValueError("meta split needs a document (or --find)")
    doc = m.state.follow(locators.parse("doc:" + a.doc.removeprefix("doc:"), m.objects)["doc"])
    name = (rows.get(doc) or {}).get("name") or doc[7:19]
    if a.cmd == "splits":
        ann = annotations.Annotations.load(m)
        for sname, bs in split.recorded(ann, doc).items():
            out({"split": sname, "pieces": len(bs), "by": sorted({b["by"] for b in bs}),
                 "bundles": [{"bundle": b["bundle"], "name": b["name"], "parts": [locators.format(p_) for p_ in b["parts"]],
                              **({"summary": b["summary"]} if b.get("summary") else {})} for b in bs]})
        return
    ps = split.pages(m, doc)
    if len(ps) < 2:
        raise ValueError(f"{name} has one page: nothing to split")
    bs = split.boundaries(ps)
    which = None
    if a.pages and a.pages != "all":
        which = {n for r in split.parse_ranges(a.pages, len(ps)) for n in range(r[0], r[1] + 1)}
    if a.sheet:
        out({"written": split.contact_sheet(m, doc, a.sheet, which), "pages": len(ps),
             "note": "read the image file(s) to look at the pages"})
        return
    if a.pages:
        for row in split.page_rows(ps, bs, which):
            out(row)
        return
    at = None
    if a.at is not None:
        at = [int(x) for x in a.at.replace(" ", "").split(",") if x]
        bad = [x for x in at if not 1 <= x <= len(ps)]
        if bad:
            raise ValueError(f"no page {bad[0]}: {name} has {len(ps)}")
    uncovered = []
    if a.plan:
        if at is not None or a.cut or a.join:
            raise ValueError("--plan is a whole split: not with --at, --cut or --join")
        plan = split.read_plan(sys.stdin.read() if a.plan == "-" else Path(a.plan).read_text(encoding="utf-8"))
        pieces, uncovered = split.from_plan(ps, plan)
        by = {b["page"]: b for b in bs}
        for r in pieces:                     # what the detector made of each cut, beside the decision
            b = by.get(r["ranges"][0][0])
            if b:
                r["starts"] = {k: b[k] for k in ("strength", "level", "for", "against")}
        starts = [r["ranges"][0][0] for r in pieces]
    else:
        starts = split.chosen(bs, a.level, at, a.cut, a.join)
        pieces = split.describe(ps, bs, starts)
        for r in pieces:
            r.pop("ranges", None)
    out({"doc": doc, "name": name, "pages": len(ps),
         "split": "--plan" if a.plan else "--at" if at is not None else a.level, "pieces": len(pieces),
         "at_each_level": {lv: len(split.starts_at(bs, lv)) + 1 for lv in split.LEVELS},
         "passed_over": split.passed_over(bs, starts),
         **({"in_no_piece": uncovered} if uncovered else {})})

    def loc_of(r):
        ranges = r.get("ranges") or [r["pages"]]
        return [f"doc:{doc}#page={x}" + (f"-{y}" if y != x else "") for x, y in ranges]

    for r in pieces:
        locs = loc_of(r)
        out({**r, "loc": locs[0], **({"locs": locs} if len(locs) > 1 else {})})
    if a.list_file:
        items = []
        for r in pieces:
            locs = loc_of(r)
            items.append({"loc": locs[0],
                          "fields": {"piece": r["piece"], "label": r["label"],
                                     "pages": ", ".join(x.split("page=")[1] for x in locs),
                                     **({"kind": r["kind"]} if r.get("kind") else {}),
                                     **({"strength": r["starts"]["strength"], "why": "; ".join(r["starts"]["for"])}
                                        if r.get("starts") else {})},
                          **({"children": [{"loc": x} for x in locs[1:]]} if len(locs) > 1 else {})})
        lists.write(a.list_file, {"type": "list", "v": 1, "title": f"{name}: {len(pieces)} pieces", "made_by": "meta split",
                                  "columns": ["piece", "pages", "label", "kind", "strength", "why"],
                                  "query": f"meta split {doc}"}, items)
        out({"written": a.list_file, "next": f"view list {a.list_file}"})
    if a.record:
        ann = annotations.Annotations.load(m)
        req = {"requested_by": a.requested_by} if a.requested_by else {}
        old = split.recorded(ann, doc).get(a.record, [])
        if old and not a.replace:
            raise ValueError(f"{name} already has a split named {a.record!r} ({len(old)} pieces): "
                             "--replace to re-record it, or another name to keep both")
        theirs = [b for b in old if annotations.is_user(b["by"]) or annotations.is_user(b.get("edited_by", ""))]
        if theirs:
            raise ValueError(f"the split {a.record!r} of {name} is the user's: record yours under another name")
        events = []
        for b in old:
            events += annapi.request(m, {**req, "op": "delete-bundle", "bundle": b["bundle"]})["events"]
        made = []
        for r in split.bundle_requests(doc, name, a.record, pieces):
            ev = annapi.request(m, {**req, **r})["events"]
            events += ev
            made.append({"bundle": ev[0]["bundle"], "name": r["name"]})
        out({"recorded": a.record, "replaced": len(old), "bundles": made})


# -- analyze ---------------------------------------------------------------------------------

class _Side(argparse.Action):
    """--side NAME starts a side; --seed, --text, --query, --include and
    --exclude after it are that side's."""

    def __call__(self, p, ns, value, opt=None):
        sides = getattr(ns, "sides", None) or []
        if self.dest == "sides":
            sides.append({"name": value, "seed": None, "text": None, "query": None, "include": [], "exclude": []})
        else:
            if not sides:
                raise argparse.ArgumentError(self, "give --side NAME first: this belongs to a side")
            key = self.dest.removeprefix("side_")
            if key in ("include", "exclude"):
                sides[-1][key].append(value)
            elif sides[-1][key]:
                raise argparse.ArgumentError(self, f"side {sides[-1]['name']!r} already has a {key}")
            else:
                sides[-1][key] = value
        ns.sides = sides


def _stances(xs):
    out_ = []
    for x in xs:
        loc, sep, st = x.rpartition("=")
        if not sep:
            raise ValueError(f"--stance {x!r}: LOC=STANCE")
        out_.append((loc if loc.lstrip().startswith(("{", "doc:", "tr:")) else "doc:" + loc, st))
    return out_


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
    s = sub.add_parser("trace", help="trace a fact: where a passage (or what a query finds) appears, how it got "
                                     "from one place to the next, and who had it when (docs/TRACE.md)")
    s.add_argument("seed", nargs="?", help="a passage: a locator with a char range (or msg=)")
    s.add_argument("--text", help="the passage's text, instead of a locator")
    s.add_argument("--query", help="a keyword query describing the fact (search's query language): "
                                   "its passages join the trace, so it follows the fact when it is reworded")
    s.add_argument("--include", action="append", default=[], metavar="LOC",
                   help="a passage judged to say the same thing (repeatable)")
    s.add_argument("--exclude", action="append", default=[], metavar="LOC",
                   help="a passage or document that doesn't belong (repeatable)")
    s.add_argument("--paraphrases", type=int, default=10, help="semantic candidates to offer for judging (0: none)")
    s.add_argument("--list", dest="list_file", help="also write a list file of the appearances, in date order")
    s.add_argument("--again", metavar="LIST", help="trace again what a list file written by --list traced, "
                                                   "with any --include/--exclude added")
    s = sub.add_parser("dispute", help="contrary claims: who holds which side and when, who is on both, who "
                                       "switched, who kept to a side after a colleague said otherwise (docs/DISPUTES.md)",
                       description="Each --side starts a claim, traced like analyze trace by the --seed, --text, "
                                   "--query, --include and --exclude after it. Two sides that can't both be true: "
                                   "denying one is the other's camp.")
    s.add_argument("--side", action=_Side, dest="sides", metavar="NAME", help="a side: a few words for its claim")
    s.add_argument("--seed", action=_Side, dest="side_seed", metavar="LOC", help="the side's seed passage")
    s.add_argument("--text", action=_Side, dest="side_text", help="the side's seed passage's text")
    s.add_argument("--query", action=_Side, dest="side_query", help="a keyword query for the side's claim: a "
                   "sentence makes the claim when the query holds within it")
    s.add_argument("--include", action=_Side, dest="side_include", metavar="LOC", help="a passage making the side's claim")
    s.add_argument("--exclude", action=_Side, dest="side_exclude", metavar="LOC", help="a passage or document that doesn't")
    s.add_argument("--stance", action="append", default=[], metavar="LOC=STANCE",
                   help="judge the statement at LOC: asserts, qualifies, denies, doubts, rejects, reports, quotes, "
                        "asks or supposes (repeatable)")
    s.add_argument("--list", dest="list_file", help="also write a list file of the statements, in date order")
    s.add_argument("--again", metavar="LIST", help="the dispute a list file written by --list holds, with any "
                                                   "--stance judgments added")
    s.add_argument("--full", action="store_true", help="also every side's appearances")
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
    elif a.cmd == "trace":
        from . import trace
        seed, text, query = a.seed, a.text, a.query
        include, exclude = list(a.include), list(a.exclude)
        if a.again:
            head, _ = lists.read(a.again)
            if seed or text or query:
                raise ValueError("--again traces what the list traced: add --include or --exclude, not a new seed")
            result, terms = trace.again(m, head, include, exclude, a.paraphrases)
            query, include, exclude = terms["query"], terms["include"], terms["exclude"]
            a.list_file = a.list_file or a.again
        else:
            if seed and not seed.lstrip().startswith(("{", "tr:")):
                seed = "doc:" + seed.removeprefix("doc:")
            s0 = trace.seed_of(m, seed, text) if (seed or text) else None
            result = trace.trace(m, s0, query=query, include=include, exclude=exclude, paraphrases=a.paraphrases)
        if a.list_file:
            head, items = trace.to_list(result, query=query, include=include, exclude=exclude)
            lists.write(a.list_file, head, items)
            result["written"] = a.list_file
            result["next"] = f"view list {a.list_file}"
        out(result)
    elif a.cmd == "dispute":
        from . import dispute
        stances = _stances(a.stance)
        if a.again:
            head, _ = lists.read(a.again)
            if getattr(a, "sides", None):
                raise ValueError("--again takes the list's sides: add --stance judgments, not sides")
            result, terms = dispute.again(m, head, stances)
            sides, stances = terms["sides"], terms["stances"]
            a.list_file = a.list_file or a.again
        else:
            sides = [dispute.side(**x) for x in (getattr(a, "sides", None) or [])]
            for x in sides:
                if x["seed"] and not x["seed"].lstrip().startswith(("{", "tr:", "doc:")):
                    x["seed"] = "doc:" + x["seed"]
            result = dispute.dispute(m, sides, stances)
        if a.list_file:
            head, items = dispute.to_list(result, sides, stances)
            lists.write(a.list_file, head, items)
            result["written"] = a.list_file
            result["next"] = f"view dispute {a.list_file}"
        if not a.full:
            del result["appearances"]
        out(result)
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
    cmds = {"adhoc": adhoc, "idx": idx, "search": search, "layers": layers, "text": text, "list": list_, "ann": ann, "page": page, "view": view, "analyze": analyze, "produce": produce, "work": work, "raw": raw, "interp": interp, "graph": graph, "meta": meta}
    if len(sys.argv) < 2 or sys.argv[1] not in cmds:
        print(f"usage: python -m adhocdocs.cli {{{','.join(cmds)}}} …", file=sys.stderr)
        sys.exit(2)
    cmds[sys.argv[1]](sys.argv[2:])


if __name__ == "__main__":
    main()
