"""Running one cell of the grid: Claude Code writes the memo, metered. See docs/QUOTEBENCH.md.

    uv run python tools/quotebench/run.py --matter MATTER --tool yes|no --delegate no|sonnet [--out runs/] [--repeat N]

The grid has two axes:

- **tool**: with the page tool (``page`` on PATH, and the session
  instructions' rule against typing quotations), or without it (no
  ``page``, and the instructions without that rule or the page tool's
  parts). Everything else is the same.
- **delegate**: Opus told to do the reading itself, or told to hand the
  reading to Sonnet subagents.

Each run gets a fresh copy of the matter and a fresh Claude Code
configuration, runs ``claude -p`` with the task prompt (prompt.md and
tasks.json) on Claude Opus 5.5, and saves in its directory:

- ``memo.md``, the memo, and ``check.json``, what check.py made of it;
- ``requests.jsonl``, every API request's model and usage, as the meter saw
  it, and ``cost.json``, their price (pricing.toml);
- ``claude.json``, Claude Code's own result (its cost estimate too, as a
  cross-check), and ``transcript/``, its session log;
- ``result.json``: the cell, the summary of check.json and the cost.

The API is reached through a meter (the screenshot recorder's proxy,
tools/screenshots/recorder.py), which holds the credential: the run's
Claude Code has a placeholder key. The credential is the first of the
recorder's that is set (ANTHROPIC_API_KEY, or the Workload Identity
Federation variables). Without one, the run stops before starting.

Claude Code runs directly on this machine (as ``scenario.py --direct``
does), not in the session container: same tools and instructions, but no
isolation, so it could read the repository. Give it a machine where that
doesn't matter.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
BIN = Path(sys.executable).parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "tools" / "screenshots"))

import check  # noqa: E402
import cost  # noqa: E402

MODEL = "claude-opus-5-5"
KEY = "sk-ant-api-on-host-00000000000000000000"
HOST_SIGNIN = "/home/claude/.claude/remote"
TOOLS = ["Bash", "Read", "Write", "Edit", "Glob", "Grep", "Agent", "Task", "TodoWrite"]
# the session's tools (container/CLAUDE.md's table); `page` only with the page tool
SESSION_TOOLS = ["search", "idx", "text", "layers", "list", "view", "ann", "graph", "page", "analyze", "meta",
                 "raw", "interp", "produce", "work", "adhoc"]

DELEGATE = {
    "no": "Do all the reading and quoting yourself, in this conversation. Don't hand any of it to subagents.",
    "sonnet": ("There is a lot to read, so to keep the cost down, hand the reading to subagents running Sonnet "
               "(the Agent tool, with model \"sonnet\"): for example, one for each document or group of items. "
               "Write the memo from what they bring back."),
}

# the same, for the chronology (prompt-chronology.md, checked with freecheck.py)
DELEGATE_CHRONOLOGY = {
    "no": "Do all the reading yourself, in this conversation. Don't hand any of it to subagents.",
    "sonnet": ("There is a lot to read, so to keep the cost down, hand the reading to subagents running Sonnet "
               "(the Agent tool, with model \"sonnet\"): for example, one for each custodian's documents. "
               "Write the chronology from what they bring back."),
}

# the same, for the FTC docket's analysis (prompt-ftc.md, checked with freecheck.py)
DELEGATE_FTC = {
    "no": "Do all the reading yourself, in this conversation. Don't hand any of it to subagents.",
    "sonnet": ("There is a lot to read, so to keep the cost down, hand the reading to subagents running Sonnet "
               "(the Agent tool, with model \"sonnet\"): for example, one for each brief, the transcripts, "
               "and the Initial Decision. Write the analysis from what they bring back."),
}


# -- the session's instructions, with and without the page tool ----------------------------------------------

NO_TOOL_EDITS = [
    ("(yours alone): pages, lists,", "(yours alone): memos, lists,"),
    # the page tool's row in the table of tools
    ("| `page` | literate pages: memos and chronologies whose quotations are references |\n", ""),
    # rule 1, and the others renumbered
    ("""1. **Never type a quotation into durable work.** In pages and notes it's a
   reference: `page ref add --from "<roughly its first words>"` (`--to …`)
   finds it, first in what you just read. In chat, say when you paraphrase.
""", ""),
    ("2. **Searches have coverage.**", "1. **Searches have coverage.**"),
    ("3. **Locators are the currency.**", "2. **Locators are the currency.**"),
    ("4. **Point the user at things.**", "3. **Point the user at things.**"),
    ("5. **The user's tags and notes are theirs.**", "4. **The user's tags and notes are theirs.**"),
    ("6. **Panes are static HTML.**", "5. **Panes are static HTML.**"),
    ("7. **Privilege calls are a lawyer's.**", "6. **Privilege calls are a lawyer's.**"),
    ("8. **Everything is recorded**", "7. **Everything is recorded**"),
]
# the summaries section is about pages: without the tool, the same advice for a Markdown memo
SUMMARIES_HEAD = "## Summaries: show the tip, link the rest"
SUMMARIES_NO_TOOL = """## Summaries: show the tip, link the rest

The user shouldn't have to trust your reading. For a summary or analysis, write
a Markdown memo in the workspace: facts quote or cite their passage, inference is
labelled, and each point cites one or two documents. Where a reader would ask
"does anything say otherwise?", say what you searched and read. `analyze trace`
and `dispute` show who had a fact and who says which side. Chat gets the tip:
about five points, each with its source.

"""


def claude_md(tool: bool, matter: Path) -> str:
    s = (REPO / "container" / "CLAUDE.md").read_text(encoding="utf-8")
    if not tool:
        for a, b in NO_TOOL_EDITS:
            if a not in s:
                raise SystemExit(f"container/CLAUDE.md has changed: update NO_TOOL_EDITS ({a[:50]!r} isn't there)")
            s = s.replace(a, b)
        start = s.index(SUMMARIES_HEAD)
        end = s.index("\n## ", start + 1) + 1
        s = s[:start] + SUMMARIES_NO_TOOL + s[end:]
        if "`page" in s or "pages" in s:
            raise SystemExit("container/CLAUDE.md still mentions the page tool without it: update NO_TOOL_EDITS")
    # the session's paths are this run's
    return s.replace("/matter/", f"{matter}/")


def prompt(tasks: dict, delegate: str) -> str:
    head = (HERE / "prompt.md").read_text(encoding="utf-8")
    items = "\n".join(f"{t['item']}. {t['request']}" for t in tasks["items"])
    return head.replace("{{DELEGATE}}", DELEGATE[delegate]).replace("{{ITEMS}}", items).replace(
        "{{N}}", str(len(tasks["items"])))


# -- the meter ------------------------------------------------------------------------------------------------

def start_meter(log: Path):
    """The recorder's proxy, keeping each answer's model and usage in requests.jsonl."""
    import recorder

    class Meter(recorder.Recorder):
        def keep(self, body, response):
            super().keep(body, response)
            with self.lock:
                self.out.write(json.dumps({"at": round(time.time(), 3), "model": body.get("model"),
                                           "main": bool(body.get("tools")), "stop": response.get("stop_reason"),
                                           "usage": response.get("usage") or {}}) + "\n")
                self.out.flush()

    rec = Meter()
    rec.out = open(log, "a", encoding="utf-8")
    srv = recorder.serve(rec, listen="127.0.0.1:0")
    return rec, srv


def credential_kind() -> str:
    import recorder
    try:
        c = recorder.Credential()
        return c.kind
    except Exception as e:
        raise SystemExit(f"no credential for the Claude API: {e}")


# -- a run ------------------------------------------------------------------------------------------------

def seed_config(home: Path, project: Path):
    home.mkdir(mode=0o700, parents=True)
    (home / ".claude.json").write_text(json.dumps({
        "hasCompletedOnboarding": True, "theme": "light", "hasSeenAutoDefaultNotice": True,
        "customApiKeyResponses": {"approved": [KEY[-20:]], "rejected": []},
        "projects": {str(project): {"hasTrustDialogAccepted": True, "hasCompletedProjectOnboarding": True}},
    }, indent=1), encoding="utf-8")
    # as the session container's managed settings: no web
    (home / "settings.json").write_text(json.dumps({"permissions": {"allow": TOOLS, "deny": ["WebSearch", "WebFetch"]}},
                                                   indent=1), encoding="utf-8")


def tool_bin(d: Path, tool: bool) -> Path:
    """The session's tools, and only those: without the page tool, no `page`."""
    d.mkdir(parents=True)
    for t in SESSION_TOOLS:
        if t == "page" and not tool:
            continue
        (d / t).symlink_to(BIN / t)
    # Python itself, for scripts Claude writes (the venv's, which has adhocdocs)
    for t in ("python", "python3"):
        (d / t).symlink_to(BIN / t)
    return d


def run(matter: Path, tool: bool, delegate: str, out: Path, tasks_path: Path, timeout: float) -> dict:
    tasks = json.loads(tasks_path.read_text(encoding="utf-8"))
    cell = f"tool-{'yes' if tool else 'no'}_delegate-{delegate}"
    rdir = out / f"{time.strftime('%Y%m%d-%H%M%S')}_{cell}"
    rdir.mkdir(parents=True)
    kind = credential_kind()
    m = rdir / "matter"
    shutil.copytree(matter, m, symlinks=True, ignore=shutil.ignore_patterns("run", "claude-home"))
    ws = m / "workspace"
    (ws / "CLAUDE.md").write_text(claude_md(tool, m), encoding="utf-8")
    config = rdir / "claude-config"
    seed_config(config, ws)
    bindir = tool_bin(rdir / "bin", tool)
    text = prompt(tasks, delegate)
    (rdir / "prompt.txt").write_text(text, encoding="utf-8")
    rec, srv = start_meter(rdir / "requests.jsonl")
    port = srv.server_address[1]
    claude = os.environ.get("ADHOC_CLAUDE_BIN") or shutil.which("claude")
    if not claude:
        raise SystemExit("needs claude on PATH (or ADHOC_CLAUDE_BIN)")
    (rdir / "home").mkdir()
    env = {"HOME": str(rdir / "home"), "PATH": f"{bindir}:/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8",
           "TZ": "UTC", "CLAUDE_CONFIG_DIR": str(config), "ADHOC_MATTER": str(m),
           "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{port}", "ANTHROPIC_API_KEY": KEY,
           "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost",
           "DISABLE_AUTOUPDATER": "1", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
           "CLAUDE_CODE_ATTRIBUTION_HEADER": "0", "DISABLE_TELEMETRY": "1"}
    cmd = ["env", "-i", *[f"{k}={v}" for k, v in env.items()], claude, "-p", "--model", MODEL,
           "--permission-mode", "dontAsk", "--output-format", "json", "--verbose"]
    # In a Claude Code cloud session, Claude Code also finds that session's own sign-in at a
    # fixed path: hide it, so the run can only use the meter (as scenario.py does).
    if Path(HOST_SIGNIN).is_dir() and os.geteuid() == 0 and shutil.which("unshare"):
        cmd = ["unshare", "-m", "--propagation", "private", "sh", "-c",
               'mount -t tmpfs none "$0" && exec "$@"', HOST_SIGNIN, *cmd]
    started = time.time()
    print(f"{cell}: running in {rdir}", file=sys.stderr, flush=True)
    try:
        p = subprocess.run(cmd, input=text, capture_output=True, text=True, cwd=ws, timeout=timeout)
        status, stdout, stderr = p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired as e:
        status, stdout, stderr = "timeout", e.stdout or "", e.stderr or ""
        stdout = stdout.decode() if isinstance(stdout, bytes) else stdout
        stderr = stderr.decode() if isinstance(stderr, bytes) else stderr
    elapsed = time.time() - started
    srv.shutdown()
    rec.out.close()
    (rdir / "claude.json").write_text(stdout, encoding="utf-8")
    (rdir / "claude.stderr").write_text(stderr, encoding="utf-8")
    (rdir / "cassette.json").write_text(json.dumps(rec.cassette("", cell)) + "\n", encoding="utf-8")
    shutil.copytree(config / "projects", rdir / "transcript", dirs_exist_ok=True) if (config / "projects").exists() else None
    requests = [json.loads(l) for l in (rdir / "requests.jsonl").read_text(encoding="utf-8").splitlines() if l]
    c = cost.summarize(requests)
    (rdir / "cost.json").write_text(json.dumps(c, indent=1) + "\n", encoding="utf-8")
    memo = ws / "memo.md"
    summary = None
    if memo.exists():
        shutil.copy(memo, rdir / "memo.md")
        res = check.check(memo.read_text(encoding="utf-8"), str(m), tasks)
        (rdir / "check.json").write_text(json.dumps(res, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        summary = res["summary"]
    claude_cost = None
    try:
        j = json.loads(stdout.strip().splitlines()[-1]) if stdout.strip() else {}
        if isinstance(j, list):
            j = next((x for x in reversed(j) if x.get("type") == "result"), {})
        claude_cost = j.get("total_cost_usd")
    except (ValueError, AttributeError):
        pass
    result = {"cell": cell, "tool": tool, "delegate": delegate, "model": MODEL, "credential": kind,
              "status": status, "seconds": round(elapsed), "memo": memo.exists(), "check": summary,
              "cost": c, "claude_code_cost_estimate": claude_cost, "problems": rec.problems,
              "tasks": str(tasks_path.relative_to(REPO)) if tasks_path.is_relative_to(REPO) else str(tasks_path)}
    (rdir / "result.json").write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
    # the matter copy is big and the same every time: keep only the workspace
    shutil.copytree(ws, rdir / "workspace", symlinks=True)
    shutil.rmtree(m)
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--matter", required=True, help="the sample matter (made from corpus/, indexed)")
    ap.add_argument("--tool", choices=["yes", "no"], required=True)
    ap.add_argument("--delegate", choices=sorted(DELEGATE), required=True)
    ap.add_argument("--tasks", default=str(HERE / "tasks.json"))
    ap.add_argument("--out", default=str(HERE / "runs"))
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--timeout", type=float, default=3 * 3600, help="seconds for one run (default 3 hours)")
    a = ap.parse_args()
    for _ in range(a.repeat):
        r = run(Path(a.matter).resolve(), a.tool == "yes", a.delegate, Path(a.out).resolve(), Path(a.tasks).resolve(),
                a.timeout)
        print(json.dumps({k: r[k] for k in ("cell", "status", "seconds", "check")} | {"dollars": r["cost"]["dollars"]}))


if __name__ == "__main__":
    main()
