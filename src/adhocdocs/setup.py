"""``adhoc setup``: check the prerequisites, then build the viewer and the
session image.

Every check runs before anything is built, and every missing piece is
reported at once, with what to install. The builds are safe to repeat:
cargo and podman reuse what is already built, so a second run takes
seconds and picks up any changes since the first.
"""

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""
    fix: str = ""        # what to do when not ok
    needed_for: str = ""


def _run(*args) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None


def check_podman() -> Check:
    c = Check("podman", False, needed_for="the session container",
              fix="install Podman (e.g. `apt install podman`, `dnf install podman`, `pacman -S podman`)")
    if not shutil.which("podman"):
        return c
    r = _run("podman", "info", "--format", "{{.Host.Security.Rootless}}")
    if r is None or r.returncode != 0:
        c.detail = "installed, but `podman info` failed: " + ((r.stderr.strip().splitlines() or [""])[-1] if r else "")
        c.fix = "run `podman info` to see what is wrong"
        return c
    if r.stdout.strip() != "true":
        c.detail = "installed, but not running rootless"
        c.fix = "run setup as your own user, not root, and see https://podman.io for rootless setup"
        return c
    c.ok, c.detail = True, "rootless"
    return c


def check_cargo() -> Check:
    c = Check("cargo", False, needed_for="building the viewer",
              fix="install Rust with rustup: https://rustup.rs (the viewer pins its version; rustup fetches it)")
    if not shutil.which("cargo"):
        return c
    c.ok = True
    c.detail = "with rustup" if shutil.which("rustup") else \
        "without rustup: the viewer's pinned toolchain (viewer/rust-toolchain.toml) won't be fetched"
    return c


def check_clang() -> Check:
    ok = bool(shutil.which("clang"))
    return Check("clang", ok, needed_for="building the viewer (its PDF library is compiled from C)",
                 fix="install clang (e.g. `apt install clang`, `dnf install clang`, `pacman -S clang`)")


def check_c_build() -> Check:
    missing = [t for t in ("cc", "make") if not shutil.which(t)]
    return Check("cc and make", not missing, detail=("missing " + ", ".join(missing)) if missing else "",
                 needed_for="building the viewer",
                 fix="install a C toolchain (e.g. `apt install build-essential`, `dnf groupinstall "
                     "'Development Tools'`, `pacman -S base-devel`)")


def check_webkit() -> Check:
    c = Check("WebKitGTK 4.1", False, needed_for="building the viewer",
              fix="install the WebKitGTK 4.1 development files (e.g. `apt install libwebkit2gtk-4.1-dev`, "
                  "`dnf install webkit2gtk4.1-devel`, `pacman -S webkit2gtk-4.1`)")
    if not shutil.which("pkg-config"):
        c.name, c.fix = "pkg-config", "install pkg-config (e.g. `apt install pkg-config`, `dnf install pkgconf`)"
        return c
    r = _run("pkg-config", "--modversion", "webkit2gtk-4.1", "gtk+-3.0")
    if r is not None and r.returncode == 0:
        c.ok, c.detail = True, r.stdout.split()[0]
    return c


def check_tesseract() -> Check:
    from .extract.pdf import tessdata
    c = Check("tesseract (English)", False, needed_for="OCR of scanned pages when you add documents",
              fix="install tesseract with English data (e.g. `apt install tesseract-ocr tesseract-ocr-eng`, "
                  "`dnf install tesseract tesseract-langpack-eng`, `pacman -S tesseract tesseract-data-eng`)")
    if not shutil.which("tesseract"):
        return c
    r = _run("tesseract", *tessdata(), "--list-langs")
    langs = r.stdout.split() if r and r.returncode == 0 else []
    if "eng" not in langs:
        c.detail = "installed, but without English data"
        c.fix = ("install tesseract's English data, or put eng.traineddata in "
                 "~/.cache/adhocdocs/models/tessdata/")
        return c
    c.ok = True
    return c


CHECKS = (check_podman, check_cargo, check_clang, check_c_build, check_webkit, check_tesseract)


def check_all() -> list[Check]:
    return [f() for f in CHECKS]


def say(msg=""):
    print(msg, file=sys.stderr, flush=True)


def build_viewer():
    say("\n== Building the viewer (cargo build --release in viewer/) ==")
    r = subprocess.run(["cargo", "build", "--release"], cwd=REPO / "viewer")
    if r.returncode != 0:
        raise SetupError("the viewer build failed (output above)")


def setup(check_only=False, viewer=True, image=True, no_cache=False) -> dict:
    """Check, then build. Returns a summary; raises SetupError on failure."""
    checks = check_all()
    say("Checking prerequisites:")
    for c in checks:
        mark = "ok     " if c.ok else "MISSING"
        say(f"  {mark} {c.name}" + (f" ({c.detail})" if c.detail else ""))
    missing = [c for c in checks if not c.ok]
    summary = {"checks": {c.name: c.ok for c in checks}}
    if missing:
        say("\nTo fix:")
        for c in missing:
            say(f"  - {c.name}, for {c.needed_for}: {c.fix}")
        raise SetupError(f"missing prerequisites: {', '.join(c.name for c in missing)}")
    if check_only:
        return summary

    if viewer:
        build_viewer()
        summary["viewer"] = str(REPO / "viewer" / "target" / "release" / "adhoc-viewer")
    if image:
        from . import container
        say("\n== Building the session image (a few minutes the first time) ==")
        try:
            container.build_image(no_cache)
        except (container.ContainerError, subprocess.CalledProcessError) as e:
            raise SetupError(f"the image build failed: {e}") from e
        summary["image"] = container.IMAGE
    say("\nSet up. Next: make a matter, e.g. the demo:\n"
        "  uv run adhoc init ~/matters/demo\n"
        "  uv run adhoc --matter ~/matters/demo ingest --manifest corpus/manifest.toml\n"
        "  uv run adhoc open ~/matters/demo")
    return summary


class SetupError(Exception):
    pass
