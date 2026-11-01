"""The audit log: chaining, hook records, the API, the proxy's records, and
the hook script the image runs."""

import asyncio
import http.client
import json
import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest

from adhocdocs import audit, container, proxy
from adhocdocs.matter import Matter

HOOK = Path(__file__).parent.parent / "container" / "audit-hook"


def pre(i, cmd="search deposition"):
    return {"hook_event_name": "PreToolUse", "session_id": "cc-1", "tool_name": "Bash",
            "tool_use_id": f"t{i}", "tool_input": {"command": cmd}}


def post(i, out="ok"):
    return {**pre(i), "hook_event_name": "PostToolUse", "tool_response": {"stdout": out, "stderr": ""}}


def test_chain_finds_edits_and_removals(tmp_path):
    log = audit.AuditLog(tmp_path / "audit")
    for i in range(5):
        log.append(session="s", kind="note", n=i)
    assert log.verify()["ok"] and log.verify()["events"] == 5
    lines = log.path.read_text(encoding="utf-8").splitlines()

    log.path.write_text("\n".join(lines[:2] + lines[3:]) + "\n", encoding="utf-8", newline="\n")              # remove one
    assert [p["line"] for p in log.verify()["problems"]] == [3]
    edited = lines[1].replace('"n": 1', '"n": 9')
    log.path.write_text("\n".join([lines[0], edited, *lines[2:]]) + "\n", encoding="utf-8", newline="\n")      # edit one
    assert [p["line"] for p in log.verify()["problems"]] == [3]
    log.path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    assert log.verify()["ok"]


def test_hook_records(tmp_path):
    log = audit.AuditLog(tmp_path / "audit")
    audit.from_hook(log, "s1", {"hook_event_name": "UserPromptSubmit", "session_id": "cc-1", "prompt": "find the POD"})
    audit.from_hook(log, "s1", pre(1))
    big = "x" * (audit.PREVIEW + 500)
    ev = audit.from_hook(log, "s1", post(1, big))
    audit.from_hook(log, "s1", pre(2, "rm -rf /"))                # never finished
    assert ev["kind"] == "tool" and ev["phase"] == "after" and ev["result_bytes"] > audit.PREVIEW
    assert len(ev["result_preview"]) == audit.PREVIEW + 1
    assert big in log.get(ev["result_sha256"]).decode()          # the whole result is kept
    report = log.verify()
    assert report["ok"] and report["tool_calls_without_result"] == [{"session": "s1", "tool_use_id": "t2"}]
    with pytest.raises(ValueError):
        audit.from_hook(log, "s1", {"hook_event_name": "Made up"})

    # a stored result changed afterwards
    p = log.objects / ev["result_sha256"][:2] / ev["result_sha256"][2:]
    p.write_text("something else", encoding="utf-8", newline="\n")
    assert log.verify()["problems"][0]["problem"] == "stored result altered"


def _serve(tmp_path):
    m = Matter.create(tmp_path / "m")
    sock = tmp_path / "audit.sock"
    threading.Thread(target=audit.serve, args=(m, sock, "ses_1"), daemon=True).start()
    for _ in range(100):                     # the file appears just before the server listens
        try:
            socket.socket(socket.AF_UNIX).connect(str(sock))
            break
        except OSError:
            time.sleep(0.05)
    return m, sock


def _post(sock, payload):
    conn = http.client.HTTPConnection("localhost")
    conn.sock = socket.socket(socket.AF_UNIX)
    conn.sock.connect(str(sock))
    conn.request("POST", "/hook", json.dumps(payload), {"Content-Type": "application/json"})
    r = conn.getresponse()
    return r.status, json.loads(r.read())


def test_api_and_cli(tmp_path):
    m, sock = _serve(tmp_path)
    assert _post(sock, pre(1))[0] == 200
    assert _post(sock, post(1))[0] == 200
    assert _post(sock, {"nonsense": 1})[0] == 400
    events = list(audit.show(m))
    assert [(e["session"], e["phase"]) for e in events] == [("ses_1", "before"), ("ses_1", "after")]
    full = list(audit.show(m, kinds=["tool"], full=True))[1]
    assert json.loads(full["result"])["stdout"] == "ok"
    assert audit.sessions(m)[0]["tool_calls"] == 1


@pytest.mark.skipif(not shutil.which("curl"), reason="needs curl")
def test_hook_script(tmp_path):
    m, sock = _serve(tmp_path)
    env = {**os.environ, "ADHOC_AUDIT_SOCKET": str(sock)}
    run = lambda payload, e=env: subprocess.run([str(HOOK)], input=json.dumps(payload), text=True,
                                                capture_output=True, env=e)
    assert run(pre(1)).returncode == 0
    assert run(post(1)).returncode == 0
    assert [e["phase"] for e in audit.show(m)] == ["before", "after"]

    # No audit API: the call is blocked before it runs, and Claude is told why.
    gone = {**env, "ADHOC_AUDIT_SOCKET": str(tmp_path / "missing.sock")}
    r = subprocess.run([str(HOOK), "block"], input=json.dumps(pre(2)), text=True, capture_output=True, env=gone)
    assert r.returncode == 2 and "audit" in r.stderr
    assert run(post(2), gone).returncode == 1      # afterwards, only reported: the call already ran


def test_proxy_records_connections(tmp_path, monkeypatch):
    log = audit.AuditLog(tmp_path / "audit")
    monkeypatch.setattr(proxy, "AUDIT", (log, "ses_1"))

    async def run():
        async def echo(r, w):
            w.write(b"echo:" + await r.read(100))
            await w.drain()
            w.close()
        upstream = await asyncio.start_server(echo, "127.0.0.1", 0)
        monkeypatch.setattr(proxy, "PORT", upstream.sockets[0].getsockname()[1])
        server = await asyncio.start_server(lambda r, w: proxy.handle(r, w, ("localhost",)), "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        for host in ("localhost", "example.com"):
            r, w = await asyncio.open_connection("127.0.0.1", port)
            w.write(f"CONNECT {host}:{proxy.PORT} HTTP/1.1\r\n\r\n".encode())
            await w.drain()
            head = await r.readuntil(b"\r\n\r\n")
            if b" 200 " in head:
                w.write(b"hello")
                await w.drain()
                await r.read(100)
            w.close()
        await asyncio.sleep(0.2)
        server.close(); upstream.close()
    asyncio.run(run())
    got = [(e["decision"], e["host"]) for e in log.read()]
    assert got == [("allowed", "localhost"), ("closed", "localhost"), ("refused", "example.com")]
    closed = list(log.read())[1]
    assert closed["bytes_sent"] == 5 and closed["bytes_received"] == 10 and log.verify()["ok"]


def test_session_has_audit_socket_but_not_the_log(tmp_path):
    m = Matter.create(tmp_path / "m")
    joined = " ".join(container.session_args(m, "s1", tmp_path / "run"))
    assert "ADHOC_AUDIT_SOCKET=/run/adhoc-audit/audit.sock" in joined
    assert f"{tmp_path / 'run' / 'audit'}:/run/adhoc-audit:ro" in joined
    assert f"{m.root}/audit" not in joined                       # the log itself is never mounted


def test_managed_settings_hook_every_event():
    root = Path(__file__).parent.parent / "container"
    settings = json.loads((root / "managed-settings.json").read_text(encoding="utf-8"))
    hooks = settings["hooks"]
    for event in ("UserPromptSubmit", "PreToolUse", "PostToolUse", "SessionStart", "SessionEnd"):
        [group] = hooks[event]
        [h] = group["hooks"]
        assert h["command"].split()[0] == "/usr/local/bin/adhoc-audit-hook", event
        if event in ("UserPromptSubmit", "PreToolUse"):              # fail closed
            assert h["command"].endswith(" block") and h["onFailure"] == "block"
    assert settings["allowManagedHooksOnly"] is True
