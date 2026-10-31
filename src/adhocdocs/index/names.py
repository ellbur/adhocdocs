"""The names index: the person-name mentions in each layer text, with
their evidence (:mod:`adhocdocs.names`). People are assembled from these at
query time (see analysis.people), so the index stays a plain table.

Schema::

    mentions(text_hash, start, end, form, given, initial, surname, basis, role)
        basis: comma-separated (title, initial, role, …, or name)
"""

from .. import names
from .base import TextIndex


class NamesIndex(TextIndex):
    kind = "names"

    def settings(self):
        return {"rules": names.VERSION}

    def wants_view(self, view):
        return view not in ("markup", "original")

    def init_db(self):
        super().init_db()
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS mentions(text_hash TEXT NOT NULL, start INTEGER, end INTEGER, form TEXT,
                                                given TEXT, initial TEXT, surname TEXT, basis TEXT, role TEXT);
            CREATE INDEX IF NOT EXISTS mentions_text ON mentions(text_hash);
            CREATE INDEX IF NOT EXISTS mentions_surname ON mentions(surname);
        """)
        self.db.commit()

    def add_text(self, text_hash):
        found = names.mentions(self.m.objects.get_text(text_hash))
        self.db.execute("INSERT INTO texts(text_hash) VALUES(?)", (text_hash,))
        self.db.executemany("INSERT INTO mentions VALUES(?,?,?,?,?,?,?,?,?)",
                            [(text_hash, x.start, x.end, x.form, x.given, x.initial, x.surname, ",".join(x.basis), x.role)
                             for x in found])

    def remove_text(self, text_hash):
        self.db.execute("DELETE FROM mentions WHERE text_hash=?", (text_hash,))
        self.db.execute("DELETE FROM texts WHERE text_hash=?", (text_hash,))

    def mentions(self, texts=None) -> list[dict]:
        cols = ("text_hash", "start", "end", "form", "given", "initial", "surname", "basis", "role")
        rows = self.db.execute("SELECT * FROM mentions ORDER BY text_hash, start")
        out = [dict(zip(cols, r)) for r in rows]
        if texts is not None:
            out = [r for r in out if r["text_hash"] in texts]
        for r in out:
            r["basis"] = r["basis"].split(",")
        return out
