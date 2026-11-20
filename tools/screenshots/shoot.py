"""Take the screenshots in a shot list, on a virtual screen, the same way
every time. See docs/SCREENSHOTS.md.

    uv run python tools/screenshots/shoot.py [--out screenshots] [--only NAME …]

It makes the sample matter from corpus/ (or reuses --matter), then for each
shot starts the viewer afresh on Xvfb, with a fixed screen size, theme,
time zone, locale and an empty home directory, runs the shot's steps, waits
until the screen stops changing, and saves a PNG. It writes index.html (a
gallery) and manifest.json (each shot's hash) beside them.

Nothing here talks to Claude: steps drive the viewer with `view` commands,
shell commands on the matter, and keys and clicks (xdotool). Shots with
Claude Code in them are scenarios (scenario.py).
"""

import argparse
import hashlib
import html
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

from harness import REPO, STEPS_DOC, Failed, Pointer, Screen, Steps, Viewer, base_env, doc_ids, log, make_matter


# -- output --------------------------------------------------------------------------------------

def gallery(out: Path, shots: list[dict]):
    rows = "\n".join(
        f'<figure id="{html.escape(s["name"])}"><a href="{html.escape(s["file"])}">'
        f'<img src="{html.escape(s["file"])}" loading="lazy" alt="{html.escape(s["caption"])}"></a>'
        f'<figcaption><b>{html.escape(s["name"])}</b> {html.escape(s["caption"])}'
        f'{"" if s["settled"] else " <i>(still changing when taken)</i>"}</figcaption></figure>'
        for s in shots)
    out.joinpath("index.html").write_text(f"""<!doctype html>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>adhocdocs screenshots</title>
<style>
body {{ font: 15px/1.4 system-ui, sans-serif; margin: 24px auto; max-width: 1200px; padding: 0 16px; }}
figure {{ margin: 0 0 32px; }} img {{ max-width: 100%; border: 1px solid #ccc; }}
figcaption {{ margin-top: 6px; }}
</style>
<h1>adhocdocs screenshots</h1>
{rows}
""", encoding="utf-8")


def git_rev() -> str:
    r = subprocess.run(["git", "-C", str(REPO), "describe", "--always", "--dirty"], capture_output=True, text=True)
    return r.stdout.strip() or "unknown"


# -- main ----------------------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(description=__doc__ + "\n" + STEPS_DOC, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--shots", default=str(Path(__file__).with_name("shots.toml")))
    p.add_argument("--out", default="screenshots")
    p.add_argument("--matter", help="the sample matter's directory, kept to reuse (default: a temporary one); "
                                    "its name heads the overview")
    p.add_argument("--only", nargs="*", help="only these shots")
    p.add_argument("--display", default=":97")
    a = p.parse_args()

    spec = tomllib.loads(Path(a.shots).read_text(encoding="utf-8"))
    w, h = spec.get("screen", {}).get("size", [1600, 1000])
    shots = [s for s in spec["shot"] if not a.only or s["name"] in a.only]
    if a.only and len(shots) != len(a.only):
        raise SystemExit(f"no such shots: {sorted(set(a.only) - {s['name'] for s in shots})}")

    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="adhoc-shots-"))
    env = {**os.environ, **base_env()}
    # The overview is headed with the matter's directory name.
    name = spec.get("matter", {}).get("name", "sample-matter")
    m = make_matter(Path(a.matter).resolve() if a.matter else tmp / name, env)
    ids = doc_ids(m)

    screen = Screen(a.display, w, h)
    done, failed = [], []
    try:
        for i, shot in enumerate(shots):
            name = shot["name"]
            log(f"[{i + 1}/{len(shots)}] {name}")
            work = tmp / "work" / name
            v = None
            try:
                v = Viewer(m, screen, tmp / "home" / name, shot.get("theme", "light"))
                # parked out of the way (hovering changes things), and jumping, not gliding: see Pointer
                steps = Steps(v, ids, work, Pointer(screen, human=False))
                for step in shot.get("steps", []):
                    steps.run(step)
                screen.settle(least=shot.get("settle", 0.6))
                v.repaint()
                rgb, settled = screen.settle()
                if not settled:
                    log("  the screen was still changing; taken anyway")
                f = out / f"{name}.png"
                screen.save(rgb, f)
                done.append({"name": name, "caption": shot.get("caption", ""), "file": f.name, "settled": settled,
                             "sha256": hashlib.sha256(rgb).hexdigest()})
            except Failed as e:
                log(f"  failed: {e}")
                failed.append(name)
                viewer_log = tmp / "home" / name / "viewer.log"
                if viewer_log.exists():
                    shutil.copyfile(viewer_log, out / f"{name}.viewer.log")
            finally:
                if v:
                    v.close()
    finally:
        screen.close()

    gallery(out, done)
    out.joinpath("manifest.json").write_text(json.dumps(
        {"commit": git_rev(), "screen": [w, h], "shots": done, "failed": failed}, indent=1) + "\n", encoding="utf-8")
    log(f"{len(done)} shots in {out}" + (f"; failed: {', '.join(failed)}" if failed else ""))
    if not a.matter:
        shutil.rmtree(tmp, ignore_errors=True)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
