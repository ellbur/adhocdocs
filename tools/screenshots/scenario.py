"""Scenarios: a session with Claude Code beside the viewer, scripted, and
captured as a video and stills. See docs/SCREENSHOTS.md.

    uv run python tools/screenshots/scenario.py replay [NAME …] [--out screenshots] [--direct]

A scenario (scenarios/NAME.toml) is a list of steps, as for still shots
(harness.STEPS_DOC), plus `prompt` steps that type at Claude's prompt. Its
recording (scenarios/NAME.cassette.json) holds what the model answered;
replaying it needs no sign-in and no network: Claude Code talks to
fakeapi.py instead of the Claude API, and every tool call it makes really
runs, against the code as it is now. If a tool prints something other than
what was recorded, or Claude Code asks for something the recording doesn't
have, the recording is stale and the scenario fails: record it again.

By default Claude Code runs in the real session container (`adhoc open`,
which needs Podman and the session image). --direct runs the claude on this
machine's PATH in the viewer's terminal instead: quicker to set up for
working on a scenario, but not what users run, so tool output can differ
(paths in particular are mapped back, /matter for the matter's directory).
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
from pathlib import Path

import fakeapi
import recorder
from harness import (BIN, HERE, REPO, STEPS_DOC, Failed, Pointer, Screen, Steps, Viewer, base_env,
                     copy_matter, doc_ids, log, make_matter)
from adhocdocs import container

SCENARIOS = HERE / "scenarios"
WORKSPACE_IN_SESSION = "/matter/workspace"
# Tools allowed outright (settings.json, below) and nothing asked. Auto mode, the default,
# checks each tool call on Anthropic's side, which can't happen through a recorder or a
# replay; and the interactive start takes the mode from here, not from settings.
CLAUDE_ARGS = ["--permission-mode", "dontAsk"]


# -- Claude Code's own settings --------------------------------------------------------------------

def seed_claude_home(home: Path, project: str, api_key: str):
    """Claude Code's settings for the session, as a user would have them after
    a first run: past its welcome, this API key accepted, the workspace
    trusted, its one-time notices seen. (Its config directory is the matter's
    claude-home, as in every session.)"""
    if home.exists():
        shutil.rmtree(home)
    home.mkdir(mode=0o700, parents=True)
    (home / ".claude.json").write_text(json.dumps({
        "hasCompletedOnboarding": True,
        "theme": "light",
        "hasSeenAutoDefaultNotice": True,
        "customApiKeyResponses": {"approved": [api_key[-20:]], "rejected": []},
        "projects": {project: {"hasTrustDialogAccepted": True, "hasCompletedProjectOnboarding": True}},
    }, indent=1), encoding="utf-8")
    # Its tools allowed outright, and nothing asked (dontAsk). Auto mode checks each tool call on
    # Anthropic's side, which can't happen through a recorder or a replay (and in the
    # session's container, approving everything is what the container is for).
    (home / "settings.json").write_text(json.dumps({
        "permissions": {
                        "allow": ["Bash", "Read", "Write", "Edit", "Glob", "Grep", "NotebookEdit", "TodoWrite"]},
    }, indent=1), encoding="utf-8")


def podman_env(here: Path) -> dict:
    """The viewer gets an empty home of its own (harness.viewer_home); Podman,
    which `adhoc open` runs with that same environment, keeps its images and
    state under the real one. A `podman` first on PATH puts the real home back
    for it alone."""
    import shlex
    real = shutil.which("podman")
    if not real:
        raise Failed("the session needs Podman (or use --direct)")
    keep = {k: os.environ.get(k) for k in ("HOME", "XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME",
                                           "XDG_RUNTIME_DIR")}
    sets = " ".join(f"{k}={shlex.quote(v)}" for k, v in keep.items() if v)
    unsets = " ".join(f"-u {k}" for k, v in keep.items() if not v)
    shim = here / "podman-shim"
    shim.mkdir(exist_ok=True)
    (shim / "podman").write_text(f"#!/bin/sh\nexec env {unsets} {sets} {shlex.quote(real)} \"$@\"\n", encoding="utf-8")
    (shim / "podman").chmod(0o755)
    return {"PATH": f"{shim}{os.pathsep}{os.environ['PATH']}"}


# -- driving Claude Code -----------------------------------------------------------------------------

class Session:
    """Claude Code in the viewer's terminal, answered by ``api``."""

    def __init__(self, api: fakeapi.Replay, turn_limit=600.0):
        self.api, self.turn_limit = api, turn_limit

    def prompt(self, text: str, steps: Steps):
        # Click at Claude's prompt (the last ❯ in the terminal), as a person does to type there.
        try:
            steps.pointer.move(*steps.v.locate({"text": "❯", "selector": "#dock .xterm-rows", "nth": -1,
                                                "at": [3.0, 0.5]}))
            steps.pointer.click()
        except Failed:
            pass             # the terminal has the focus when the viewer opens
        self.api.turn_end.clear()
        steps.v.screen.xdotool("type", "--delay", steps.typing_delay_ms if steps.pointer.human else 5, text)
        time.sleep(0.4 if steps.pointer.human else 0.1)
        steps.v.screen.xdotool("key", "Return")
        t = said = time.time()
        while not self.api.turn_end.wait(1):
            if self.api.fatal:
                raise Failed(self.api.fatal[0])
            if not steps.v.alive():
                raise Failed("the session ended while Claude was answering")
            if time.time() - t > self.turn_limit:
                raise Failed(f"Claude's answer to {text[:40]!r} didn't finish within {self.turn_limit:.0f} s")
            if time.time() - said >= 60:
                said = time.time()
                n = getattr(self.api, "requests", getattr(self.api, "n", 0))
                log(f"    still waiting for Claude: {time.time() - t:.0f} s, {n} API requests so far")
        steps.v.screen.settle(least=0.5, quiet=1.5, limit=30)


# -- one scenario --------------------------------------------------------------------------------

def run(name: str, base, out: Path, tmp: Path, direct: bool, display: str, human=True, accept=False,
        turn_limit=600.0, record=False) -> dict:
    """Replay a scenario's recording, or (``record``) record it: the same
    session, with the Claude API (through recorder.py) answering instead of
    fakeapi.py."""
    spec = tomllib.loads((SCENARIOS / f"{name}.toml").read_text(encoding="utf-8"))
    cassette_path = SCENARIOS / f"{name}.cassette.json"
    pace = spec.get("pace", {})
    w, h = spec.get("screen", {}).get("size", [1600, 1000])

    here = tmp / name
    m = copy_matter(base, here)
    ids = doc_ids(m)
    if record:
        try:
            replay = recorder.Recorder(here / "api.log")
        except RuntimeError as e:
            raise Failed(str(e))
        log(f"  recording, with {replay.cred.kind}")
        human = False            # the recording keeps what was said, not how fast it was typed
    else:
        if not cassette_path.exists():
            raise Failed(f"{name} has no recording yet ({cassette_path.name}): record it first")
        cassette = json.loads(cassette_path.read_text(encoding="utf-8"))
        replay = fakeapi.Replay(cassette, fakeapi.Pace(pace.get("think", 1.2), pace.get("cps", 180.0),
                                                       pace.get("tool", 0.4)),
                                here / "api.log")
    serve = recorder.serve if record else fakeapi.serve
    if direct:
        # Tool output says where the matter is; in a session that's /matter.
        replay.aliases = {str(m.root): "/matter"}
    screen = Screen(display, w, h)
    v = None
    shots = []
    try:
        home = here / "viewer-home"
        if direct:
            srv = serve(replay, listen="127.0.0.1:0")
            port = srv.server_address[1]
            key = "sk-ant-replay-0000000000000000000000"
            seed_claude_home(m.root / "claude-home", str(m.root / "workspace"), key)
            claude = os.environ.get("ADHOC_CLAUDE_BIN") or shutil.which("claude")
            if not claude:
                raise Failed("--direct needs claude on PATH (or ADHOC_CLAUDE_BIN)")
            env = {"HOME": str(here / "claude-user"), "PATH": f"{BIN}:/usr/local/bin:/usr/bin:/bin",
                   "TERM": "xterm-256color", "LANG": "C.UTF-8", "TZ": "UTC",
                   "CLAUDE_CONFIG_DIR": str(m.root / "claude-home"), "ADHOC_MATTER": str(m.root),
                   "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{port}", "ANTHROPIC_API_KEY": key,
                   "DISABLE_AUTOUPDATER": "1", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
                   "DISABLE_TELEMETRY": "1"}
            (here / "claude-user").mkdir(exist_ok=True)
            sock = here / "viewer.sock"
            env["ADHOC_VIEW_SOCKET"] = str(sock)
            run_cmd = ["env", "-i", *[f"{k}={val}" for k, val in env.items()], claude, *CLAUDE_ARGS]
            v = Viewer(m, screen, home, run=run_cmd, cwd=m.root / "workspace", sock=sock)
        else:
            api_sock = here / "api.sock"
            srv = serve(replay, unix=str(api_sock))
            seed_claude_home(m.root / "claude-home", WORKSPACE_IN_SESSION, container.API_KEY_PLACEHOLDER)
            if os.geteuid() == 0:
                # Podman as root (a container of its own, say) maps root to no one in the session;
                # rootless, the session's user is ours. Give the copy to the session's user.
                subprocess.run(["chown", "-R", f"{container.IMAGE_UID}:{container.IMAGE_UID}", str(m.root)], check=True)
            # `adhoc open` starts the viewer, on a socket in this matter's run directory
            launch_env_home = home / "run"
            os.environ["XDG_RUNTIME_DIR"] = str(launch_env_home)
            sock = container.run_dir(m) / "1" / "viewer.sock"
            launcher = [str(BIN / "adhoc"), "open", str(m.root), "--terminal", "beside", "--user", "user:screenshots",
                        "--api-socket", str(api_sock), "--test-hooks", "--", "claude", *CLAUDE_ARGS]
            v = Viewer(m, screen, home, sock=sock, launcher=launcher, extra_env=podman_env(here))
        workdir = here / "work"
        video = spec.get("video", True) and not record
        if video:
            screen.record(out / f"{name}.mp4", fps=spec.get("fps", 30))
        pointer = Pointer(screen, human=human)
        # Claude Code has started when its prompt shows
        t = time.time()
        while True:
            try:
                v.locate({"text": "❯", "selector": "#dock .xterm-rows"})
                break
            except Failed:
                if time.time() - t > 60:
                    raise Failed("Claude Code's prompt didn't appear within 60 s")
                if not v.alive():
                    raise Failed(f"the session ended before Claude Code started; see {v.log}")
                time.sleep(0.5)
        screen.settle(least=0.5, quiet=1.0, limit=15)

        def on_shot(shot_name, step):
            rgb, _ = screen.settle(least=float(step.get("settle", 0.3)), quiet=0.8, limit=10)
            f = out / f"{name}-{shot_name}.png"
            screen.save(rgb, f)
            shots.append({"name": shot_name, "file": f.name, "caption": step.get("caption", "")})

        steps = Steps(v, ids, workdir, pointer, session=Session(replay, turn_limit),
                      typing_delay_ms=int(pace.get("typing_ms", 45)), on_shot=on_shot)
        for i, step in enumerate(spec.get("step", [])):
            log(f"  step {i + 1}: {json.dumps(step)[:100]}")
            steps.run(step)
        time.sleep(float(spec.get("tail", 1.5)))
    except Failed:
        screen.save(screen.grab(), out / f"{name}.failed.png")      # what the screen showed
        raise
    finally:
        screen.stop_recording()
        if v:
            v.close()
        if not direct:
            subprocess.run(["podman", "rm", "-f", "-t", "1", f"{container.slug(m)}-1"],
                           capture_output=True)
        screen.close()
        for f in ("api.log", "viewer-home/viewer.log"):
            if (here / f).exists():
                shutil.copyfile(here / f, out / f"{name}.{Path(f).name}")
        run_log = m.root / "run"
        if run_log.exists():
            for f in run_log.glob("*.log"):
                shutil.copyfile(f, out / f"{name}.run-{f.name}")
    if accept:
        cassette_path.write_text(json.dumps(replay.accepted(), indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        log(f"  wrote what the tools printed into {cassette_path.name}")
    if record and not replay.problems:
        version = (HERE / "claude-code-version").read_text(encoding="utf-8").strip()
        cassette_path.write_text(json.dumps(replay.cassette(version), indent=1, ensure_ascii=False) + "\n",
                                 encoding="utf-8")
        log(f"  wrote {cassette_path.relative_to(REPO)}: {len(replay.turns)} turns, {len(replay.side)} side requests")
    report = {"scenario": name, **replay.report(), "shots": shots,
              "video": f"{name}.mp4" if video else None}
    (out / f"{name}.report.json").write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__ + "\n" + STEPS_DOC, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("replay", help="replay scenarios' recordings: video, stills, and a check that they're current")
    r.add_argument("names", nargs="*", help="default: every scenario with a recording")
    r.add_argument("--out", default="screenshots")
    r.add_argument("--matter", help="the sample matter's directory, kept to reuse (each scenario works on a copy)")
    r.add_argument("--direct", action="store_true", help="claude from this machine's PATH, not the session container")
    r.add_argument("--display", default=":98")
    r.add_argument("--fast", action="store_true", help="no human pacing (a quick check, not for showing)")
    r.add_argument("--turn-limit", type=float, default=600.0, help="seconds Claude's answer to a prompt may take")
    r.add_argument("--accept", action="store_true",
                   help="write what the tools printed into the recording, as the expected output: for a "
                        "recording written by hand, never to quiet a stale one (record it again)")
    r = sub.add_parser("record", help="record scenarios: the same sessions with the Claude API answering, "
                                      "written to scenarios/NAME.cassette.json (see recorder.py for the credential)")
    r.add_argument("names", nargs="*", help="default: every scenario")
    r.add_argument("--out", default="recorded", help="stills and logs from the recording session")
    r.add_argument("--matter", help="the sample matter's directory, kept to reuse (each scenario works on a copy)")
    r.add_argument("--direct", action="store_true", help="claude from this machine's PATH, not the session container")
    r.add_argument("--display", default=":98")
    r.add_argument("--turn-limit", type=float, default=900.0, help="seconds Claude's answer to a prompt may take")
    a = p.parse_args()
    record = a.cmd == "record"

    pattern = "*.toml" if record else "*.cassette.json"
    names = a.names or sorted(f.name.split(".")[0] for f in SCENARIOS.glob(pattern))
    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="adhoc-scenario-"))
    env = {**os.environ, **base_env()}
    base = make_matter(Path(a.matter).resolve() if a.matter else tmp / "base" / "sample-matter", env)
    failed = []
    for name in names:
        log(f"scenario {name}")
        try:
            report = run(name, base, out, tmp, a.direct, a.display, human=not getattr(a, "fast", False),
                         accept=getattr(a, "accept", False), turn_limit=a.turn_limit, record=record)
        except Failed as e:
            log(f"  failed: {e}")
            failed.append(name)
            # the logs' ends, here, so CI's log says what happened without the artifact
            for f in (f"{name}.api.log", f"{name}.viewer.log", f"{name}.run-viewer.log", f"{name}.run-proxy.log"):
                if (out / f).exists():
                    lines = (out / f).read_text(encoding="utf-8", errors="replace").splitlines()[-15:]
                    if lines:
                        log(f"  --- {f} (end)\n" + "\n".join("    " + x[:300] for x in lines))
            continue
        if record:
            if report["problems"]:
                log(f"  not written: {json.dumps(report['problems'])[:500]}")
                failed.append(name)
            else:
                log(f"  recorded: {report['turns_recorded']} turns")
            continue
        if report["problems"] or not report["complete"]:
            log(f"  stale: {len(report['problems'])} difference(s) from the recording"
                + ("" if report["complete"] else f"; {report['turns_replayed']} of {report['turns_recorded']} turns replayed")
                + f" (see {name}.report.json)")
            failed.append(name)
        else:
            log(f"  ok: {report['turns_replayed']} turns, {len(report['shots'])} stills")
    shutil.rmtree(tmp, ignore_errors=True)
    if failed:
        log(f"failed: {', '.join(failed)}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
