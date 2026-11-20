"""The grid's results, from the runs' result.json files. See docs/QUOTEBENCH.md.

    uv run python tools/quotebench/report.py [RUNS_DIR] [--markdown]

One row per cell (tool × delegate), every run counted: none is left out.
Accuracy is per run and per quotation; cost is the meter's, at
pricing.toml's prices.
"""

import argparse
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
CELLS = [(False, "no"), (False, "sonnet"), (True, "no"), (True, "sonnet")]


def load(runs: Path) -> list[dict]:
    out = []
    for p in sorted(runs.glob("*/result.json")):
        r = json.loads(p.read_text(encoding="utf-8"))
        r["dir"] = p.parent.name
        out.append(r)
    return out


def cell_rows(results: list[dict]) -> list[dict]:
    rows = []
    for tool, delegate in CELLS:
        rs = [r for r in results if r["tool"] == tool and r["delegate"] == delegate]
        if not rs:
            continue
        done = [r for r in rs if r.get("check")]
        ch = [r["check"] for r in done]
        items = sum(c["items"] for c in ch)
        words = sum(c["words_quoted"] for c in ch)
        dollars = [r["cost"]["dollars"] for r in rs]
        sonnet = [sum(v["dollars"] for k, v in r["cost"]["by_model"].items() if "sonnet" in k) for r in rs]
        rows.append({
            "tool": "yes" if tool else "no", "delegate": delegate, "runs": len(rs), "memos": len(done),
            "runs_with_a_misquote": sum(c["items_misquoted"] > 0 for c in ch),
            "items_misquoted": sum(c["items_misquoted"] for c in ch), "items": items,
            "quotations_misquoted": sum(c["quotations_misquoted"] for c in ch),
            "quotations": sum(c["quotations"] for c in ch),
            "words_wrong_per_1000": round(1000 * sum(c["words_wrong"] for c in ch) / words, 2) if words else None,
            "citations_wrong": sum(c["citations_wrong"] for c in ch),
            "broken_references": sum(c["broken_references"] for c in ch),
            "typed_quotations": sum(c["by_kind"].get("typed", 0) + c["by_kind"].get("typed-inline", 0) for c in ch),
            "unanswered": sum(c["items"] - c["answered"] for c in ch),
            "mean_coverage": round(sum(c["mean_coverage"] for c in ch) / len(ch), 3) if ch else None,
            "mean_dollars": round(sum(dollars) / len(dollars), 2),
            "mean_sonnet_share": round(sum(s / d for s, d in zip(sonnet, dollars) if d) / len(rs), 3),
            "mean_minutes": round(sum(r["seconds"] for r in rs) / len(rs) / 60, 1),
        })
    return rows


def markdown(rows: list[dict]) -> str:
    head = ("| page tool | reading by | runs | runs with a misquote | items misquoted | quotations misquoted "
            "| words wrong / 1000 | citations wrong | broken refs | typed quotations | coverage | $ / run | Sonnet share "
            "| min / run |")
    lines = [head, "|" + "---|" * (head.count("|") - 1)]
    for r in rows:
        lines.append(f"| {r['tool']} | {'Opus' if r['delegate'] == 'no' else 'Sonnet subagents'} | {r['runs']} "
                     f"| {r['runs_with_a_misquote']}/{r['memos']} | {r['items_misquoted']}/{r['items']} "
                     f"| {r['quotations_misquoted']}/{r['quotations']} | {r['words_wrong_per_1000']} "
                     f"| {r['citations_wrong']} | {r['broken_references']} | {r['typed_quotations']} "
                     f"| {r['mean_coverage']} | {r['mean_dollars']} | {r['mean_sonnet_share']:.0%} | {r['mean_minutes']} |")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("runs", nargs="?", default=str(HERE / "runs"))
    ap.add_argument("--markdown", action="store_true")
    a = ap.parse_args()
    rows = cell_rows(load(Path(a.runs)))
    print(markdown(rows) if a.markdown else json.dumps(rows, indent=1))


if __name__ == "__main__":
    main()
