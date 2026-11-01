"""The fields index: one row per document, from the document object and its
metadata. Addresses are lowercased, one row each in ``addrs``.

Schema::

    docs(doc PRIMARY KEY, content, custodian, collection, path, parent, part,
         kind, name, date, subject, title, author, pages, signature)
    addrs(doc, field, addr)        field is from, to, cc or bcc

``signature`` is the signatures a file's first bytes match (raw.SIGNATURES),
one per line. Tags are not here: search reads them live from the annotation
log.

The index is cheap to make again from the change feed, so one built with an
older schema is rebuilt on first use rather than refused.
"""

from .base import Index

COLUMNS = ("doc", "content", "custodian", "collection", "path", "parent", "part",
           "kind", "name", "date", "subject", "title", "author", "pages", "signature")
ADDRESS_FIELDS = ("from", "to", "cc", "bcc")


class FieldsIndex(Index):
    kind = "fields"

    def settings(self):
        return {"schema": 3}

    def check_settings(self):
        if self.manifest()["settings"] == self.settings():
            return
        with self.lock():
            if self.manifest()["settings"] != self.settings():
                self.db.executescript("DROP TABLE IF EXISTS docs; DROP TABLE IF EXISTS addrs; DELETE FROM meta;")
                self.db.commit()
                self._write_manifest({})
                self.init_db()

    def init_db(self):
        super().init_db()
        self.db.executescript(f"""
            CREATE TABLE IF NOT EXISTS docs({", ".join(c + (" TEXT PRIMARY KEY" if c == "doc" else "") for c in COLUMNS)});
            CREATE TABLE IF NOT EXISTS addrs(doc TEXT, field TEXT, addr TEXT);
            CREATE INDEX IF NOT EXISTS addrs_doc ON addrs(doc);
            CREATE INDEX IF NOT EXISTS addrs_addr ON addrs(addr);
            CREATE INDEX IF NOT EXISTS docs_content ON docs(content);
        """)
        self.db.commit()

    def apply(self, ev):
        op = ev["op"]
        if op == "add-document":
            obj = self.m.objects.get_object(ev["doc"])
            src = obj.get("source") or {}
            custodian = (obj.get("custodian") or "").removeprefix("cust:") or None
            collection, path = src.get("collection"), src.get("path")
            if obj.get("parent"):
                # attachments carry their parent's custodian and source
                row = self.db.execute("SELECT custodian, collection, path FROM docs WHERE doc=?", (obj["parent"],)).fetchone()
                if row:
                    custodian, collection = custodian or row[0], collection or row[1]
                    path = path or f"{row[2]}/{obj['part']}"
            self.db.execute(
                "INSERT OR REPLACE INTO docs(doc, content, custodian, collection, path, parent, part) VALUES(?,?,?,?,?,?,?)",
                (ev["doc"], obj["content"], custodian, collection, path, obj.get("parent"), obj.get("part")))
        elif op == "set-metadata":
            meta = self.m.objects.get_object(ev["metadata"])
            self.db.execute(
                "UPDATE docs SET kind=?, name=?, date=?, subject=?, title=?, author=?, pages=?, signature=? WHERE doc=?",
                (meta.get("kind"), meta.get("name"), meta.get("date"), meta.get("subject"),
                 meta.get("title"), meta.get("author"), meta.get("pages"),
                 "\n".join(meta.get("signature") or []) or None, ev["doc"]))
            self.db.execute("DELETE FROM addrs WHERE doc=?", (ev["doc"],))
            self.db.executemany("INSERT INTO addrs VALUES(?,?,?)",
                                [(ev["doc"], f, a) for f in ADDRESS_FIELDS for a in meta.get(f, [])])
        elif op in ("withdraw", "supersede"):
            doc = ev.get("doc") or ev.get("old")
            self.db.execute("DELETE FROM docs WHERE doc=?", (doc,))
            self.db.execute("DELETE FROM addrs WHERE doc=?", (doc,))

    def row(self, doc: str) -> dict | None:
        cur = self.db.execute(f"SELECT {', '.join(COLUMNS)} FROM docs WHERE doc=?", (doc,))
        r = cur.fetchone()
        if r is None:
            return None
        d = dict(zip(COLUMNS, r))
        for f in ADDRESS_FIELDS:
            d[f] = [a for (a,) in self.db.execute("SELECT addr FROM addrs WHERE doc=? AND field=?", (doc, f))]
        return d

    def rows(self) -> dict[str, dict]:
        out = {r[0]: dict(zip(COLUMNS, r)) for r in self.db.execute(f"SELECT {', '.join(COLUMNS)} FROM docs")}
        for d in out.values():
            for f in ADDRESS_FIELDS:
                d[f] = []
        for doc, f, a in self.db.execute("SELECT doc, field, addr FROM addrs"):
            if doc in out:
                out[doc][f].append(a)
        return out

    def verify(self, repair=False):
        state = self.m.state
        want = set(state.live_documents())
        have = {r[0] for r in self.db.execute("SELECT doc FROM docs")}
        missing, extra = want - have, have - want
        if repair and (missing or extra):
            with self.db:
                self.db.execute("DELETE FROM docs")
                self.db.execute("DELETE FROM addrs")
                for ev in self.m.changes():
                    self.apply(ev)
        return {"name": self.name, "missing": len(missing), "extra": len(extra), "repaired": repair}
