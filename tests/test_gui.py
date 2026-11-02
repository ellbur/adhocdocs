"""Starting things without a command line (adhocdocs.gui): the launcher's
list of matters, terminals, and where `adhoc open` runs Claude Code."""

import json
import stat
import sys

import pytest

from adhocdocs import cli, container, gui, setup
from adhocdocs.matter import Matter


def test_the_list_of_matters(tmp_path):
    a, b = Matter.create(tmp_path / "a"), Matter.create(tmp_path / "b")
    gui.remember(a.root)
    gui.remember(b.root)
    gui.remember(a.root, opened=True)
    rows = gui.matters()["matters"]
    assert [r["name"] for r in rows] == ["a", "b"]          # opened last comes first
    assert rows[0]["documents"] == 0 and rows[0]["exists"] and rows[0]["opened"]
    gui.forget(a.root)
    assert [r["name"] for r in gui.matters()["matters"]] == ["b"]
    assert (tmp_path / "a" / "store").is_dir()              # forgetting leaves the matter where it is
    (tmp_path / "b" / "store").rename(tmp_path / "b" / "gone")
    assert gui.matters()["matters"][0]["exists"] is False


def test_commands_put_matters_on_the_list(tmp_path, capsys):
    cli.adhoc(["init", str(tmp_path / "m")])
    capsys.readouterr()
    cli.adhoc(["matters"])
    rows = json.loads(capsys.readouterr().out)["matters"]
    assert [r["path"] for r in rows] == [str((tmp_path / "m").resolve())]
    cli.adhoc(["matters", "forget", str(tmp_path / "m")])
    assert json.loads(capsys.readouterr().out)["matters"] == []
    cli.adhoc(["matters", "add", str(tmp_path / "m")])
    assert len(json.loads(capsys.readouterr().out)["matters"]) == 1
    with pytest.raises(SystemExit):
        cli.adhoc(["matters", "add", str(tmp_path)])          # not a matter
    assert "not a matter" in capsys.readouterr().err


def _fake(dir, name):
    p = dir / name
    p.write_text("#!/bin/sh\n", encoding="utf-8")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return p


@pytest.mark.skipif(sys.platform != "linux", reason="terminal programs on Linux")
def test_finding_a_terminal_program(tmp_path, monkeypatch):
    monkeypatch.delenv("ADHOC_TERMINAL", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))
    assert gui.terminal_app() is None
    _fake(tmp_path, "xterm")
    _fake(tmp_path, "konsole")
    assert gui.terminal_app() == ("konsole", [str(tmp_path / "konsole"), "-e"])     # in order of preference
    assert gui.in_terminal_app(["adhoc", "open"]) == [str(tmp_path / "konsole"), "-e", "adhoc", "open"]
    # Debian's x-terminal-emulator is named for what it links to
    (tmp_path / "x-terminal-emulator").symlink_to(_fake(tmp_path, "gnome-terminal.wrapper"))
    assert gui.terminal_app() == ("gnome-terminal", [str(tmp_path / "x-terminal-emulator"), "-e"])
    monkeypatch.setenv("ADHOC_TERMINAL", "my-term --new-window -x")
    assert gui.terminal_app() == ("my-term", ["my-term", "--new-window", "-x"])
    monkeypatch.delenv("ADHOC_TERMINAL")
    monkeypatch.setenv("PATH", str(tmp_path / "nowhere"))
    with pytest.raises(RuntimeError, match="ADHOC_TERMINAL"):
        gui.in_terminal_app(["x"])


def test_open_in_a_terminal_program(tmp_path, monkeypatch, capsys):
    m = Matter.create(tmp_path / "m")
    spawned = []
    import adhocdocs.portable
    monkeypatch.setattr(adhocdocs.portable, "spawn", lambda argv, *a, **k: spawned.append(argv) or type("P", (), {"pid": 7})())
    monkeypatch.setenv("ADHOC_TERMINAL", "term -e")
    with pytest.raises(SystemExit) as e:
        cli.adhoc(["open", str(m.root), "--terminal", "app", "--user", "dana", "--", "claude", "--resume"])
    assert e.value.code == 0
    # the terminal program runs the same command, in itself, and holds its window open if the session fails
    assert spawned == [["term", "-e", *gui.adhoc_command(), "open", str(m.root), "--terminal", "here", "--hold",
                        "--user", "dana", "--", "claude", "--resume"]]
    assert json.loads(capsys.readouterr().out) == {"terminal": "term", "pid": 7}
    assert gui.matters()["matters"][0]["opened"]


def test_where_claude_code_runs(tmp_path, monkeypatch):
    m = Matter.create(tmp_path / "m")
    calls = []
    monkeypatch.setattr(container, "open_session", lambda m, user, viewer, command, terminal: calls.append(terminal) or 0)
    for args, isatty, want in [([], True, "here"), ([], False, "beside"), (["--terminal", "window"], True, "window"),
                               (["--terminal", "here"], False, "here")]:
        monkeypatch.setattr(sys.stdin, "isatty", lambda: isatty, raising=False)
        monkeypatch.setattr(sys.stdout, "isatty", lambda: isatty, raising=False)
        with pytest.raises(SystemExit):
            cli.adhoc(["open", str(m.root), *args])
        assert calls.pop() == want


def test_the_viewer_runs_the_session_with_a_terminal_of_its_own(tmp_path, monkeypatch):
    m = Matter.create(tmp_path / "m")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    # started from the launcher, there's no terminal here, but the viewer gives podman one
    assert "-it" in container.session_args(m, "s1", tmp_path / "run", tty=True)
    assert "-it" not in container.session_args(m, "s1", tmp_path / "run")
    with pytest.raises(container.ContainerError, match="--no-viewer"):
        container.open_session(m, "dana", viewer=False, terminal="beside")


def test_desktop_entry(monkeypatch):
    if sys.platform != "linux":
        assert gui.install_desktop_entry() is None
        return
    p = gui.install_desktop_entry()
    text = p.read_text(encoding="utf-8")
    assert p.name == "adhocdocs.desktop" and "Terminal=false" in text
    assert f"Exec={sys.executable} -m adhocdocs.cli adhoc gui" in text


def test_setup_check_says_what_is_built(monkeypatch):
    monkeypatch.setattr(setup, "check_all", lambda: [setup.Check("podman", True)])
    monkeypatch.setattr(container, "image_exists", lambda: False)
    s = setup.setup(check_only=True)
    assert s["image"] is None and "viewer" in s
    monkeypatch.setattr(setup, "check_all", lambda: [setup.Check("podman", False, fix="install it", needed_for="sessions")])
    with pytest.raises(setup.SetupError) as e:
        setup.setup(check_only=True)
    assert e.value.missing == [{"name": "podman", "needed_for": "sessions", "fix": "install it", "detail": ""}]
    assert "image" in e.value.summary
