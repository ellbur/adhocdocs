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
