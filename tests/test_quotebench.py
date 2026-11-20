"""tools/quotebench: comparing quotations with their source, finding them in a
memo, and pricing a run. See docs/QUOTEBENCH.md."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "quotebench"))

import align  # noqa: E402
import check  # noqa: E402
import cost  # noqa: E402
import run  # noqa: E402

SOURCE = """Rule 26 FEDERAL RULES OF CIVIL PROCEDURE

(C) Witnesses Who Do Not Provide a Written Report. Unless
otherwise stipulated or ordered by the court, if the witness
is not required to provide a written report, this disclosure
must state:
(i) the subject matter on which the witness is ex-
pected to present evidence under Federal Rule of Evi-
dence 702, 703, or 705; and
(ii) a summary of the facts and opinions to which the
witness is expected to testify.

Shippers and carriers are free to deal directly with each other and “[o]nly where the
shipper finds that it can get better service” does it turn to a broker.
"""
SRC = align.Source("frcp", SOURCE)


def compare(q):
    return align.compare(SRC, q)


def test_verbatim_despite_line_breaks_hyphens_and_quote_marks():
    for q in ["the subject matter on which the witness is expected to present evidence under Federal Rule of "
              "Evidence 702, 703, or 705; and",
              "the subject matter on which the witness is ex- pected to present evidence",     # the hyphen kept
              "the subject matter on which the witness is ex-pected to present evidence",
              'free to deal directly with each other and "[o]nly where the shipper finds that it can get better '
              'service" does it turn to a broker.']:
        r = compare(q)
        assert r["verbatim"], (q, r["errors"])
        assert r["punctuation"] == 0


def test_word_errors():
    r = compare("the subject matter on which witness is expected to present evidence under Federal Rule")
    assert not r["verbatim"] and r["errors"][0]["type"] == "dropped" and r["errors"][0]["source"] == "the"
    r = compare("the subject matter on which the witness is expected to offer evidence under Federal Rule")
    assert [(e["type"], e["source"], e["quote"]) for e in r["errors"]] == [("changed", "present", "offer")]
    r = compare("the subject matter on which the expert witness is expected to present evidence")
    assert [(e["type"], e["quote"]) for e in r["errors"]] == [("added", "expert")]
    r = compare("XYZZY the subject matter on which the witness is expected to present evidence")
    assert not r["verbatim"] and r["words_wrong"] == 1


def test_marked_changes_are_not_misquotes():
    r = compare("[T]he subject matter on which [the expert] is expected to present evidence")
    assert r["verbatim"] and len(r["marked"]) == 1
    r = compare("the subject matter on which the witness is expected . . . to testify.")
    assert r["verbatim"] and r["segments"] == 2       # an ellipsis: two runs, in order
    r = compare("to testify … the subject matter on which the witness is expected")
    assert not r["verbatim"] and r["out_of_order"]


def test_case_and_punctuation_are_reported_apart():
    r = compare("The subject matter on which the witness is expected to present evidence")
    assert r["verbatim"] and r["case"] == [{"source": "the", "quote": "The"}]
    r = compare("the subject matter on which the witness is expected to present evidence under Federal Rule of "
                "Evidence 702, 703 or 705 and")
    assert r["verbatim"] and r["punctuation"] == 2


def test_coverage():
    passage = "(ii) a summary of the facts and opinions to which the witness is expected to testify."
    assert align.coverage(passage, passage) == 1.0
    assert 0.4 < align.coverage(passage, "a summary of the facts and opinions") < 0.6


def test_finding_quotations_in_a_memo():
    memo = """# Memo

## Item 1

> The first quotation, typed,
> over two lines.
>
> — uscourts-federal-rules-of-civil-procedure.pdf

## Item 2

The text says “a long inline quotation of more than twelve words that runs on and on here” (title9).

## Item 3

::q{#q3}
"""
    secs = check.items_of(memo)
    assert sorted(secs) == [1, 2, 3]
    q1 = check.typed_quotations(secs[1])
    assert q1[0]["text"] == "The first quotation, typed,\nover two lines."
    assert check.cited_name(q1[0]["citation"], ["uscourts-federal-rules-of-civil-procedure.pdf"])
    assert check.typed_quotations(secs[2])[0]["kind"] == "typed-inline"
    assert check.typed_quotations(secs[3]) == []
    # Markdown escapes are the characters they escape
    assert check.strip_markdown(r"and “\[o\]nly where”") == "and “[o]nly where”"


def test_cost():
    p = cost.prices()
    u = {"input_tokens": 1000, "output_tokens": 300, "cache_read_input_tokens": 5000,
         "cache_creation_input_tokens": 2000}
    # 1000 × $4 + 5000 × $0.20 + 2000 × $4 × 1.25 + 300 × $20, per million
    assert abs(cost.request_cost(p, "claude-opus-5-5", u) - 0.021) < 1e-9
    u1h = {**u, "cache_creation": {"ephemeral_1h_input_tokens": 2000, "ephemeral_5m_input_tokens": 0}}
    assert abs(cost.request_cost(p, "claude-opus-5-5", u1h) - 0.027) < 1e-9   # writes at 2x
    assert cost.request_cost(p, "claude-sonnet-5-5", u) == 0.0105
    s = cost.summarize([{"model": "claude-opus-5-5", "usage": u}, {"model": "mystery", "usage": u}], p)
    assert s["dollars"] == 0.021 and s["unpriced_models"] == ["mystery"]


def test_instructions_without_the_page_tool():
    with_tool = run.claude_md(True, Path("/m"))
    without = run.claude_md(False, Path("/m"))
    assert "page ref add" in with_tool and "/m/store" in with_tool
    assert "`page" not in without and "Never type a quotation" not in without
    assert "1. **Searches have coverage.**" in without and "7. **Everything is recorded**" in without
