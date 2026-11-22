"""Find exact character ranges for passages given as (opening words, closing words)
in a document's primary layer text; write page ref batch rows."""
import json, re, subprocess, sys

DOCS = {}
for line in open(sys.argv[1]):
    loc, name = line.split(None, 1)
    DOCS[name.strip()] = loc

def doc(key):
    hits = [n for n in DOCS if key in n]
    assert len(hits) == 1, (key, hits)
    loc = DOCS[hits[0]]
    path = subprocess.run(['text', '--path', loc], capture_output=True, text=True).stdout.strip()
    return loc, open(path).read()

Q = {"'": "['’‘]", "’": "['’‘]", "‘": "['’‘]", '"': '["“”]', '“': '["“”]', '”': '["“”]'}
def pat(phrase):
    out = []
    for w in phrase.split():
        out.append(''.join(Q.get(c, re.escape(c)) for c in w))
    return r'\s+'.join(out)

cache = {}
rows, errs = [], []
for spec in json.load(open(sys.argv[2])):
    rid, key, a, b = spec[:4]
    occ = spec[4] if len(spec) > 4 else 0
    if key not in cache: cache[key] = doc(key)
    loc, t = cache[key]
    starts = [m for m in re.finditer(pat(a), t)]
    if len(starts) <= occ:
        errs.append((rid, 'start not found', len(starts))); continue
    s = starts[occ]
    e = re.compile(pat(b)).search(t, s.start())
    if not e or e.end() - s.start() > 3000:
        errs.append((rid, 'end not found')); continue
    rows.append({"id": rid, "doc": loc, "start": s.start(), "end": e.end()})
    if len(starts) > 1 and len(spec) <= 4:
        errs.append((rid, 'NOTE multiple starts, took first', len(starts)))
with open(sys.argv[3], 'w') as f:
    for r in rows: f.write(json.dumps(r) + '\n')
for e in errs: print(*e)
print(len(rows), 'rows')
