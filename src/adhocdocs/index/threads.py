"""The threads index: each email's Message-ID, In-Reply-To, References and
normalized subject, from its metadata. Threads are built from these at query
time (see analysis.threads), so the index stays a plain table.

Schema::

    emails(doc PRIMARY KEY, message_id, in_reply_to, refs, subject, date)
        refs is a JSON list; subject has Re:/Fwd: prefixes removed, lowercased
"""

import json
import re

from .base import Index

PREFIX = re.compile(r"^\s*((re|fwd?|aw|wg|sv|antw)\s*(\[\d+\])?\s*:\s*)+", re.I)


def normalize_subject(s: str | None) -> str:
    return re.sub(r"\s+", " ", PREFIX.sub("", s or "")).strip().lower()


class ThreadsIndex(Index):
    kind = "threads"

    def settings(self):
        return {"schema": 1}

    def init_db(self):
        super().init_db()
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS emails(doc TEXT PRIMARY KEY, message_id TEXT, in_reply_to TEXT, refs TEXT,
                                              subject TEXT, date TEXT);
            CREATE INDEX IF NOT EXISTS emails_mid ON emails(message_id);
        """)
        self.db.commit()

    def apply(self, ev):
        op = ev["op"]
        if op == "set-metadata":
            meta = self.m.objects.get_object(ev["metadata"])
            if meta.get("kind") != "email":
                return
            self.db.execute("INSERT OR REPLACE INTO emails VALUES(?,?,?,?,?,?)",
                            (ev["doc"], meta.get("message_id"), meta.get("in_reply_to"),
                             json.dumps(meta.get("references", [])), normalize_subject(meta.get("subject")),
                             meta.get("date")))
        elif op in ("withdraw", "supersede"):
            self.db.execute("DELETE FROM emails WHERE doc=?", (ev.get("doc") or ev.get("old"),))

    def rows(self) -> list[dict]:
        cols = ("doc", "message_id", "in_reply_to", "refs", "subject", "date")
        out = [dict(zip(cols, r)) for r in self.db.execute("SELECT * FROM emails")]
        for r in out:
            r["refs"] = json.loads(r["refs"] or "[]")
        return out

    def verify(self, repair=False):
        state = self.m.state
        have = {r[0] for r in self.db.execute("SELECT doc FROM emails")}
        extra = have - set(state.live_documents())
        if repair and extra:
            with self.db:
                self.db.executemany("DELETE FROM emails WHERE doc=?", [(d,) for d in extra])
        return {"name": self.name, "extra": len(extra), "repaired": repair}
