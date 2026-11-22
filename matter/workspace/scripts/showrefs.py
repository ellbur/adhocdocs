import json, subprocess, sys, glob
want = sys.argv[1].split(',')
rows = {}
for f in glob.glob('/home/user/adhocdocs/matter/workspace/parts/*.resolved.jsonl'):
    for l in open(f):
        d = json.loads(l); rows[d['id']] = d
for i in want:
    d = rows.get(i)
    if not d: print(i, 'MISSING'); continue
    loc = {"doc": d["doc"], "layer": d["layer"], "select": {"char": [d["start"], d["end"]]}}
    t = subprocess.run(['text', json.dumps(loc)], capture_output=True, text=True).stdout
    t = ' '.join(t.split())
    print(f"{i}: {t[:420]}{' …' if len(t)>420 else ''}\n")
