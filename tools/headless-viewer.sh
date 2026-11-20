#!/bin/sh
# Run the viewer on a virtual screen, to check it where there is no display
# (a cloud container, CI). See docs/VIEWER.md, "Checking it without a screen".
#
#   tools/headless-viewer.sh MATTER [:99]
#
# Then drive it with `uv run view --matter MATTER …`, click with
# `DISPLAY=:99 xdotool mousemove X Y click 1`, and take a picture with
# `DISPLAY=:99 import -window root shot.png`. Stop it with Ctrl-C.
set -e
matter=$(cd "$1" && pwd)
display=${2:-:99}
here=$(cd "$(dirname "$0")/.." && pwd)
bin=${ADHOC_VIEWER_BIN:-$here/viewer/target/release/adhoc-viewer}
[ -x "$bin" ] || { echo "no viewer at $bin: cd viewer && cargo build --release" >&2; exit 1; }
if ! xdpyinfo -display "$display" >/dev/null 2>&1; then
    Xvfb "$display" -screen 0 1600x1000x24 >/dev/null 2>&1 &
    trap 'kill $!' EXIT
    until xdpyinfo -display "$display" >/dev/null 2>&1; do :; done
fi
annotate=$(cd "$here" && uv run python -c 'from adhocdocs.view import annotate_command; print(annotate_command())')
DISPLAY=$display "$bin" --matter "$matter" --user user:headless --annotate "$annotate"
