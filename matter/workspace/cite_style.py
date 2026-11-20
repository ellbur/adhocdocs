"""Citation style for memo.md: the document's cite-as name (its file name),
then for PDFs the printed page where the text layer has its label, always
with the PDF page, so a page without a label is never shown as a printed one."""
import html


def cite(info: dict) -> str:
    s = info.get("cite_as") or info.get("name") or ""
    pages, labels = info.get("pages"), info.get("page_labels")
    if pages and info.get("kind") == "pdf":
        a, b = pages
        pdf = f"PDF p. {a}" if a == b else f"PDF pp. {a}–{b}"
        if labels:
            la, lb = labels
            s += (f" at {la}" if la == lb else f" at {la}–{lb}") + f" ({pdf})"
        else:
            s += f" ({pdf})"
    if info.get("emphasis_added"):
        s += " (emphasis added)"
    return html.escape(s)
