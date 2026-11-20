"""The screenshots as a web page, for GitHub Pages: each scenario's video,
playable, with its stills, then the still shots. Files keep their names, so
their addresses stay the same from run to run and the README can show them
(https://OWNER.github.io/REPO/NAME.gif).

    python tools/screenshots/site.py --from screenshots --out site

reads what shoot.py (manifest.json) and scenario.py (NAME.report.json)
wrote there, and copies the pictures and videos, not the logs."""

import argparse
import html
import json
import shutil
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
MEDIA = (".png", ".jpg", ".gif", ".mp4")


def esc(s) -> str:
    return html.escape(str(s or ""))


def scenarios(src: Path) -> list[dict]:
    """Each replayed scenario that passed, in the order of scenarios/, with its caption."""
    out = []
    for toml in sorted((HERE / "scenarios").glob("*.toml")):
        name = toml.stem
        rep = src / f"{name}.report.json"
        if not rep.exists():
            continue
        r = json.loads(rep.read_text(encoding="utf-8"))
        if r.get("problems") or not r.get("complete", True):
            continue
        spec = tomllib.loads(toml.read_text(encoding="utf-8"))
        out.append({"name": name, "caption": spec.get("caption", ""), "video": r.get("video"),
                    "gif": r.get("gif"), "shots": r.get("shots") or []})
    return out


def page(scens: list[dict], stills: list[dict], commit: str) -> str:
    parts = []
    for s in scens:
        poster = s["shots"][0]["file"] if s["shots"] else ""
        video = (f'<video controls muted loop playsinline preload="metadata" src="{esc(s["video"])}"'
                 f'{f" poster={chr(34)}{esc(poster)}{chr(34)}" if poster else ""}></video>') if s["video"] else ""
        shots = "".join(f'<figure><a href="{esc(x["file"])}"><img src="{esc(x["file"])}" loading="lazy" alt="{esc(x.get("caption"))}"></a>'
                        f'<figcaption>{esc(x.get("caption"))}</figcaption></figure>' for x in s["shots"])
        files = " · ".join(f'<a href="{esc(f)}">{esc(f)}</a>' for f in (s["video"], s["gif"]) if f)
        parts.append(f'<section id="{esc(s["name"])}"><h3>{esc(s["name"])}</h3><p>{esc(s["caption"])}</p>'
                     f'{video}<p class="files">{files}</p><div class="shots">{shots}</div></section>')
    still = "".join(f'<figure id="{esc(x["name"])}"><a href="{esc(x["file"])}"><img src="{esc(x["file"])}" loading="lazy" '
                    f'alt="{esc(x["caption"])}"></a><figcaption><b>{esc(x["name"])}</b> {esc(x["caption"])}</figcaption></figure>'
                    for x in stills)
    return f"""<!doctype html>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>adhocdocs screenshots</title>
<style>
body {{ font: 15px/1.45 system-ui, sans-serif; margin: 24px auto; max-width: 1200px; padding: 0 16px; color: #1d2330; background: #fff; }}
h2 {{ margin: 40px 0 4px; }} h3 {{ margin: 28px 0 4px; font-size: 18px; }} video, img {{ max-width: 100%; border: 1px solid #ccc; border-radius: 4px; }}
video {{ width: 100%; }} .files {{ font-size: 13px; color: #667; }}
.shots {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 16px; }}
figure {{ margin: 0 0 24px; }} figcaption {{ margin-top: 6px; font-size: 14px; }}
@media (prefers-color-scheme: dark) {{ body {{ color: #dfe3ea; background: #15181e; }} img, video {{ border-color: #333; }} a {{ color: #8fb0ff; }} }}
</style>
<h1>adhocdocs screenshots</h1>
<p>Made by the <a href="https://github.com/ellbur/adhocdocs/blob/main/docs/SCREENSHOTS.md">Screenshots workflow</a>
from commit <code>{esc(commit)}</code>: sessions with Claude Code replayed from recordings, and still shots of the viewer.</p>
<h2>Sessions</h2>
{"".join(parts) or "<p>None this run.</p>"}
<h2>Still shots</h2>
{still}
"""


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--from", dest="src", default="screenshots")
    p.add_argument("--out", default="site")
    a = p.parse_args()
    src, out = Path(a.src), Path(a.out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for f in src.iterdir():
        if f.suffix in MEDIA and not f.name.endswith(".failed.png"):
            shutil.copyfile(f, out / f.name)
    manifest = src / "manifest.json"
    m = json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() else {}
    if manifest.exists():
        shutil.copyfile(manifest, out / "manifest.json")
    scens = scenarios(src)
    (out / "index.html").write_text(page(scens, m.get("shots", []), m.get("commit", "")), encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")
    print(f"{out}: {len(scens)} sessions, {len(m.get('shots', []))} still shots")


if __name__ == "__main__":
    main()
