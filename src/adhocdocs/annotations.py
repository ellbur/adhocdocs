"""Reading the annotation log (see ANNOTATIONS.md). Writing is not built yet."""

from .matter import Matter


def current_tags(m: Matter) -> dict[str, set[str]]:
    """Document ID -> tags applied to the whole document, replayed from the log."""
    tags: dict[str, set[str]] = {}
    for ev in m.annotation_log.read():
        target = ev.get("target") or {}
        if set(target) != {"doc"}:
            continue
        if ev["op"] == "tag":
            tags.setdefault(target["doc"], set()).add(ev["tag"])
        elif ev["op"] == "untag":
            tags.get(target["doc"], set()).discard(ev["tag"])
    return tags
