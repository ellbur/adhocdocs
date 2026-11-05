"""Step through a matter's paged documents in the viewer, as a reader pressing
], and report how long each took to show its first page (open_ms).

    tools/headless-viewer.sh MATTER &          # or a viewer on a real screen
    uv run python tools/viewer_bench.py MATTER [--display :99] [--pause 0.4]

The viewer must be running for MATTER, its window focused. See VIEWER.md
(the 100 ms target)."""

import argparse
import json
import os
import subprocess
import time

from adhocdocs import view
from adhocdocs.analysis import _ready
from adhocdocs.matter import Matter


def main():
    p = argparse.ArgumentParser()
    p.add_argument("matter")
    p.add_argument("--display", default=os.environ.get("DISPLAY", ":99"))
    p.add_argument("--pause", type=float, default=0.4, help="seconds on each document, as a reader would")
    a = p.parse_args()
    m = Matter(a.matter)
    rows = _ready(m, "fields").rows()
    docs = sorted((d for d in m.state.live_documents() if (rows.get(d) or {}).get("pages")),
                  key=lambda d: rows[d].get("path") or "")
    locs = ["doc:" + d for d in docs]

    def pane():
        return next(x for x in view.command(m, {"cmd": "state"})["panes"] if x["kind"] == "document")

    def wait(n, limit=20):
        t = time.time()
        while time.time() - t < limit:
            x = pane()
            if len(x.get("open_ms") or []) >= n:
                return x
            time.sleep(0.05)
        raise SystemExit(f"document {n} didn't show within {limit} s")

    view.command(m, {"cmd": "sequence", "locs": locs})
    x = wait(1)
    time.sleep(a.pause)
    for k in range(2, len(locs) + 1):
        subprocess.run(["xdotool", "key", "bracketright"], env={**os.environ, "DISPLAY": a.display}, check=True)
        x = wait(k)
        time.sleep(a.pause)
    ms = x["open_ms"][-len(locs):]
    for d, t in zip(docs, ms):
        print(json.dumps({"ms": t, "name": rows[d].get("name"), "pages": rows[d].get("pages")}))
    s = sorted(ms)
    print(json.dumps({"documents": len(ms), "median": s[len(s) // 2], "max": s[-1], "over_100": sum(t > 100 for t in ms)}))


if __name__ == "__main__":
    main()
