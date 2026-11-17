"""tools/screenshots: the replay's stream and the recorder's reading of it
agree, and the replay notices when a tool prints something else."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "screenshots"))

import fakeapi  # noqa: E402
import recorder  # noqa: E402

ANSWER = {"content": [
    {"type": "thinking", "thinking": "", "signature": "sig"},
    {"type": "text", "text": "I'll search for it — and show the list."},
    {"type": "tool_use", "id": "toolu_1", "name": "Bash",
     "input": {"command": "search 'forklift recertification' --list f.list.jsonl", "description": "Search"}}],
    "stop_reason": "tool_use", "usage": {"input_tokens": 10, "output_tokens": 20}}


def test_streamed_answer_reads_back_whole():
    out = []
    fakeapi.stream(out.append, ANSWER, "claude-opus-5-5", fakeapi.Pace(0, 0, 0), "msg_1")
    m = recorder.Message()
    for chunk in out:              # in pieces, as a socket delivers them
        for i in range(0, len(chunk), 7):
            m.feed(chunk[i:i + 7])
    assert m.result() == ANSWER


def body(prompt=None, results=()):
    content = [{"type": "text", "text": "<system-reminder>\nnot typed\n</system-reminder>"}]
    if prompt:
        content.append({"type": "text", "text": prompt})
    content += [{"type": "tool_result", "tool_use_id": i, "content": [{"type": "text", "text": t}]} for i, t in results]
    return {"tools": [{"name": "Bash"}], "messages": [{"role": "user", "content": content}]}


def test_replay_notices_stale_tool_output():
    cassette = {"turns": [
        {"prompt": "Find it.", "results": [], "response": ANSWER},
        {"results": [{"tool_use_id": "toolu_1", "content": '{"pane": "p2", "as_of": "ev_01M738A707YXYD8DJVJ1QFM2F2"}'}],
         "response": {"content": [{"type": "text", "text": "Done."}], "stop_reason": "end_turn"}}]}
    r = fakeapi.Replay(cassette, fakeapi.Pace(0, 0, 0), None)
    r.answer(body("Find it."))
    # an ID that carries the time it was made differs from run to run: not a change
    r.answer(body(results=[("toolu_1", '{"pane": "p2", "as_of": "ev_01M7H432A6BW2M4QGNKBEDFCK8"}')]))
    assert r.report()["problems"] == [] and r.report()["complete"]

    r = fakeapi.Replay(cassette, fakeapi.Pace(0, 0, 0), None)
    r.answer(body("Find it."))
    r.answer(body(results=[("toolu_1", '{"pane": "p3"}')]))
    assert [p["kind"] for p in r.report()["problems"]] == ["tool result"]


def test_replay_notices_another_prompt_and_extra_turns():
    cassette = {"turns": [{"prompt": "Find it.", "results": [], "response": {
        "content": [{"type": "text", "text": "Done."}], "stop_reason": "end_turn"}}]}
    r = fakeapi.Replay(cassette, fakeapi.Pace(0, 0, 0), None)
    r.answer(body("Find something else."))
    r.answer(body("And more."))
    assert [p["kind"] for p in r.report()["problems"]] == ["prompt", "extra request"]


def test_federated_requests_leave_out_the_claude_code_beta():
    incoming = {"Host": "127.0.0.1:3129", "x-api-key": "placeholder", "Content-Length": "10",
                "anthropic-beta": "claude-code-20250219,interleaved-thinking-2025-05-14,effort-2025-11-24",
                "anthropic-version": "2023-06-01", "x-app": "cli"}
    h = recorder.upstream_headers(incoming, {"authorization": "Bearer sk-ant-oat01-x"}, federated=True)
    assert h["anthropic-beta"] == "interleaved-thinking-2025-05-14,effort-2025-11-24,oauth-2025-04-20"
    assert h["authorization"] == "Bearer sk-ant-oat01-x" and "x-api-key" not in h and "Host" not in h
    assert h["x-app"] == "cli" and h["accept-encoding"] == "identity"
    # with an API key, Claude Code's betas go as it sent them
    h = recorder.upstream_headers(incoming, {"x-api-key": "sk-ant-api-x"}, federated=False)
    assert h["anthropic-beta"] == incoming["anthropic-beta"] and h["x-api-key"] == "sk-ant-api-x"


def test_what_changes_on_its_own_is_not_a_difference():
    a = '{"events": [{"id": "ev_01M7KMBZ3G8BCXGCPJ2EA5A54X", "at": "2026-11-17T02:11:25.936671Z", "sig": "BXzDOv_2sYgM1eUl_2hsv3n-G_fq2MAcY54zO33jn1rg4CvlC10pAq76kpaTeKtwhdWwIZBEbLRW7Wr5179nDw", "origin": "o_PYQK369XWW9FEGWMD8V230PQMQ", "name": "X"}]}'
    b = '{"events": [{"id": "ev_01M7KME0X8XMG4P8KKN7WS3YHP", "at": "2026-11-17T02:12:33.320462Z", "sig": "3P05-gedkkihr5ZtkDATfYt74AvnlK6-kijlSRHIB8Y1fW7Uwsbi2vUenJj2QJI_vEpKJ1-RNXJcJtu7Ug1pAg", "origin": "o_HM6SNVPZ9GD187GV90YAEEFH1J", "name": "X"}]}'
    assert fakeapi.normalize(a) == fakeapi.normalize(b)
    assert fakeapi.normalize(a) != fakeapi.normalize(a.replace('"name": "X"', '"name": "Y"'))
