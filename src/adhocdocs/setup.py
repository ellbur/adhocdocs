"""``adhoc setup``: check the prerequisites, then build the viewer and the
session image.

Every check runs before anything is built, and every missing piece is
reported at once, with what to install. The builds are safe to repeat:
cargo and podman reuse what is already built, so a second run takes
seconds and picks up any changes since the first.
"""

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .portable import WINDOWS, tesseract

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
    if WINDOWS:
        c.fix = ("install Podman for Windows (https://podman.io, or `winget install RedHat.Podman`), "
                 "then `podman machine init` and `podman machine start`")
    if not shutil.which("podman"):
        return c
    r = _run("podman", "info", "--format", "{{.Host.Security.Rootless}}")
    if r is None or r.returncode != 0:
        c.detail = "installed, but `podman info` failed: " + ((r.stderr.strip().splitlines() or [""])[-1] if r else "")
        c.fix = ("start its VM: `podman machine start` (or `podman machine init` first)" if WINDOWS
                 else "run `podman info` to see what is wrong")
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
                 fix="install LLVM (`winget install LLVM.LLVM`) and add its bin directory to PATH" if WINDOWS else
                 "install clang (e.g. `apt install clang`, `dnf install clang`, `pacman -S clang`)")


def check_msvc() -> Check:
    """Rust on Windows links with Microsoft's C++ build tools."""
    c = Check("Visual Studio C++ build tools", False, needed_for="building the viewer",
              fix="install the Visual Studio Build Tools with the \"Desktop development with C++\" workload "
                  "(`winget install Microsoft.VisualStudio.2022.BuildTools`; rustup offers this too)")
    vswhere = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
    if not vswhere.exists():
        return c
    r = _run(str(vswhere), "-latest", "-products", "*", "-requires",
             "Microsoft.VisualStudio.Component.VC.Tools.x86.x64", "-property", "installationVersion")
    if r is not None and r.returncode == 0 and r.stdout.strip():
        c.ok, c.detail = True, r.stdout.strip()
    return c


def check_webview2() -> Check:
    """The viewer's web view on Windows; part of Windows 11, and of Edge's updater on 10."""
    c = Check("WebView2 runtime", False, needed_for="running the viewer",
              fix="install the Microsoft Edge WebView2 Runtime: https://developer.microsoft.com/microsoft-edge/webview2/")
    import winreg
    key = r"Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    for root, sub in ((winreg.HKEY_LOCAL_MACHINE, "SOFTWARE\\WOW6432Node\\" + key),
                      (winreg.HKEY_LOCAL_MACHINE, "SOFTWARE\\" + key),
                      (winreg.HKEY_CURRENT_USER, "Software\\" + key)):
        try:
            with winreg.OpenKey(root, sub) as k:
                version = winreg.QueryValueEx(k, "pv")[0]
        except OSError:
            continue
        if version and version != "0.0.0.0":
            c.ok, c.detail = True, version
            return c
    return c


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
    if WINDOWS:
        c.fix = ("install tesseract with English data (e.g. UB Mannheim's installer: "
                 "https://github.com/UB-Mannheim/tesseract/wiki)")
    exe = tesseract()
    if not shutil.which(exe):
        return c
    r = _run(exe, *tessdata(), "--list-langs")
    langs = r.stdout.split() if r and r.returncode == 0 else []
    if "eng" not in langs:
        c.detail = "installed, but without English data"
        c.fix = ("install tesseract's English data, or put eng.traineddata in "
                 "~/.cache/adhocdocs/models/tessdata/")
        return c
    c.ok = True
    return c


if WINDOWS:
    CHECKS = (check_podman, check_cargo, check_msvc, check_clang, check_webview2, check_tesseract)
else:
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
        summary["viewer"] = str(REPO / "viewer" / "target" / "release" / ("adhoc-viewer.exe" if WINDOWS else "adhoc-viewer"))
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
