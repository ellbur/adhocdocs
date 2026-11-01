# Syncing a Matter Across Installations

*Status: being built, in the steps under "Order of building". Built: step 1,
one log per installation ([`matter.py`](src/adhocdocs/matter.py),
[`origin.py`](src/adhocdocs/origin.py)), and step 2, conflicts resolved on
replay ([`annotations.py`](src/adhocdocs/annotations.py),
[`sync.py`](src/adhocdocs/sync.py)), and step 3, publishing work product
([`work.py`](src/adhocdocs/work.py)). Decisions still open are at the
end.*

A matter can be open on several machines: a lawyer's laptop, a paralegal's
desktop, a firm server. Cloud document platforms solve this by keeping the
documents on a server and showing them in a browser. That makes review slow:
skimming fifty documents means waiting fifty times for the network. adhocdocs
stays **local first**. Every installation holds a full copy of the matter
and works from it, and installations **sync** with each other in the
background, peer to peer, as Syncthing does.

Two things differ from Syncthing:

1. **One pool per matter.** In Syncthing, sharing a folder A→B and B→C
   doesn't connect A and C. Here, every installation of a matter joins the
   matter's one **pool**, and every member learns of every other member.
   There is no chain of pairwise shares to get wrong.
2. **A hub, optionally.** A pool can include a **hub**: an always-on server
   with a stable address. Anyone can **push** a matter to it, without its
   administrator pulling it in, and others join through it. A hub is a
   member like any other, just always reachable.

## What syncs and what doesn't

| Data | Synced | Why |
|---|---|---|
| Documents: originals, text layers, metadata (`store/`) | yes | the matter itself; OCR and extraction run once, where ingested |
| Tags, notes, tag definitions, entities, dated events (`annotations/`) | yes | judgments about the documents |
| Published work product: pages, chronologies, key sets, lists | yes, **when published** | see below |
| Pool membership | yes | so every member knows every other |
| The workspace (`workspace/`) | **no** | the private working space of whoever is running the session |
| Indexes | no | derived, and rebuilt locally from the synced feeds |
| `claude-home/` (sign-in, transcripts) | **never** | credentials, and each person's own conversations |
| The audit log (`audit/`) | no | a record of *this* machine's sessions (see open questions) |
| Viewer state, `run/` | no | local |

### The workspace stays private; work product is published

Pages, chronologies and key sets are *made* in the workspace, where Claude
edits freely. But the workspace is the user's scratch space: half-finished
drafts, scripts, experiments. It isn't synced, and nothing from outside
writes into it while Claude works there.

Publishing applies only to what's made in the workspace. Documents,
annotations and membership live outside it and sync on their own, with no
step from anyone.

So work product crosses over by **publishing**
([`work.py`](src/adhocdocs/work.py)): `work publish memo.md` (from Claude,
through the annotation API, which stamps authorship), or `adhoc publish
memo.md --user <id>` (from the user, on the host). A publication is a
snapshot of a file or directory, stored by hash in the store, recorded as an
event in the `work` feed (`work/logs/<origin>.jsonl`, one log per
installation like the others), and synced. Published work is laid out on
every installation in `<matter>/work/`, mounted read-only into sessions,
and the viewer's overview has a Published section. To keep working on
someone's page, `work checkout memo.md` copies it into the workspace.

- **Versions form a history.** Each publication names the versions it
  revises. The workspace remembers which version each file was checked out
  from or last published as, so publishing again revises that one.
  Publishing over a name someone else published, without checking it out,
  is refused: there'd be nothing to say what it revises.
- **Forks.** Two installations revising the same version without seeing
  each other's make a fork: the later is current in `work/`, the other is
  kept in `work/.forks/`, `work ls` and `adhoc sync conflicts` list it, and
  the overview says so. Someone resolves it by publishing a version that
  revises both (`--merge`); Claude is good at merging two drafts. Nothing is
  merged silently, since a page's prose is someone's judgment.
- **Publishing carries what the work needs.** A page can quote a layer made
  in the workspace (a re-OCR, say). Publishing promotes the layers a page or
  list names into the store, with `add-layer` events in this installation's
  store log, so the quotations resolve everywhere. A page's `cite-style`
  file goes with it. A directory (a production) publishes whole.
- **Retiring** a name takes it out of `work/`; the history stays.
- Locators are content-addressed (document and layer hashes), so a
  published page's quotations mean the same thing on every installation.
- Chronologies and key sets in `work/` count on the date cards like the
  workspace's own.

## The data model: one log per installation

A matter used to have one `store/log.jsonl` and one
`annotations/log.jsonl`. Now it has **one log per installation**, each
written only by the installation that owns it:

```
<matter>/
  matter.toml                   the matter's ID (the pool's ID)
  store/objects/                unchanged: content-addressed, so a union merges them
  store/logs/<origin>.jsonl     the store feed, one file per installation
  annotations/logs/<origin>.jsonl
  work/logs/<origin>.jsonl      publications; snapshots in store/objects
  pool/logs/<origin>.jsonl      membership
  sync/                         keys, peer state (host only, never mounted)
```

An **origin** is one installation of one matter, identified by the hash of
its public key (as a Syncthing device ID is). It's made the first time the
installation writes, not when it reads.

- **A copied matter gets a new origin.** The origin records the host and
  path it was made for, and a matter opened anywhere else makes its own. A
  restored backup or a copied directory would otherwise be a second writer
  of the original's log, forking it. Moving a matter costs a new log file,
  nothing more.
- **Existing matters migrate when opened on the host:** each single log
  becomes the logs of a new origin, unchanged, and `matter.toml` records
  which (`legacy_origin`), so index cursors that named the old feeds still
  work. In a session, where nothing is writable, an unmigrated matter is
  read as it is.

This makes sync almost trivial and conflict-free *at the storage level*:

- **Objects are immutable and named by hash.** Two installations that hold
  the same object hold the same bytes. Merging stores is a union. The same
  file ingested on two machines gets the same document ID (it's a hash of
  the content and its custodian, collection and path), so it isn't added
  twice.
- **Each log has a single writer, and only grows.** What a peer has is a
  vector: for each origin, how many events. Syncing is "here's my vector;
  send me what's past it", then fetching the objects those events name.
- **Every event is signed** by its origin's key, and each log is a hash
  chain (as the audit log is). So any member can pass on any other member's
  events, and the receiver can check them. A's changes reach C through B
  even when A and C can never connect directly. Direct connections are an
  optimization, not a requirement.

Conflicts are left to the **meaning** of the events, where they belong.

### Replaying many logs into one state

Every installation replays the union of the logs in the **same order**, so
every installation that has the same events reaches the same state.

- **Event IDs come from a hybrid logical clock.** They are still ULIDs, but
  an installation never stamps an event earlier than one it has already
  seen. So an event made after seeing another sorts after it, whatever the
  machines' clocks say. (A machine whose clock is far ahead drags later IDs
  along with it. That's harmless: IDs order events, and the time an event
  happened is its `at`.)
- **The merge order:** at each step, the earliest of the logs' next events,
  by ID, ties going to the lower origin, each log in its own file order. For
  logs the clock wrote, that's ID order. Logs from before (whose IDs came
  from several processes' clocks) keep their file order, and every
  installation still picks the same order. The viewer merges the same way.
- **The rules are checked again on replay** (`Annotations.replay` in
  [`annotations.py`](src/adhocdocs/annotations.py)). An event that was valid
  where it was written can conflict with one written concurrently
  elsewhere: the same tag defined twice, the same hotkey, an untag of a tag
  someone else just applied. Replaying in the common order, each event is
  applied, applied in part, or has no effect, the same way on every
  installation. It stays in its log either way, and `adhoc sync conflicts`
  lists what happened and why. Doing the same thing twice (untagging twice,
  defining a tag the same way twice) is quiet.
- Specific cases:
  - **Tag and untag** on the same target: the later wins.
  - **Exclusive tag groups** are enforced on replay, not only by the untag
    events the writer recorded, since two installations can each apply a
    different tag from the group. The later tag wins, unless it's Claude's
    and would remove a person's: then Claude's has no effect.
  - **Claude can't undo a person's work from elsewhere either:** Claude's
    untag of a tag a person re-applied meanwhile, or its edit of a field a
    person set meanwhile, has no effect.
  - **A tag defined on two installations:** the first definition stands,
    and the second's color, hotkey and description become a conflict to
    look at. Its applications still count, since it's the same name.
  - **A hotkey taken twice:** the later tag is defined without it.
  - **An entity defined on two installations** under the same name and
    kind: the later one's ID becomes an alias of the earlier's, it adds the
    names and fields the earlier lacks, and edits naming either ID land on
    one entity.
  - **A note deleted on one installation and edited on another:** whichever
    is later wins. A reply to a deleted note stays, on the same document:
    replies carry their thread's target for this.
  - **Two edits of a note or an entity field:** the later wins; the earlier
    is in the history.
  - **Withdraw and supersede** take effect whenever they arrive.
  - **A privilege call by Claude** (from a log written outside the write
    path, or from before the rule) is kept as a flag and reported:
    productions act only on a person's.
- **The viewer follows the same replay.** The rules live in one place.
  Replaying, Python writes `annotations/replay.json`, naming each event that
  replays as something other than itself and what it replays as, and the
  viewer substitutes those as it replays. Only arriving logs change it
  (events written through the write path are checked and sort last), so the
  write path and sync keep it current.
- **Cursors are per log.** A reader's cursor maps each log
  (`store/<origin>`, `workspace`) to the last event it read, and every event
  says which log it came from, so an event that arrives late but sorts early
  is still past the cursor. `ann log --cursor` gives one for the annotation
  feed.
- **Indexes skip overtaken events.** Indexes apply each event once, as it
  arrives. A document's `add-document` can arrive after its `withdraw`, so
  an index checks each event against the replayed state and skips one that
  later events have overtaken (`still_true` in
  [`index/base.py`](src/adhocdocs/index/base.py)). Custom indexes get such
  events marked `stale`.

### Authorship and the rules across installations

The annotation rules (Claude can't change a person's work; only a person
makes privilege calls) still hold, for every person in the pool.

- **People are members too.** The pool log binds each person (`user:<id>`,
  with a display name) to the installations they use. An event authored
  `user:alice` is accepted only from an installation bound to Alice.
- **Claude's events say where they came from:** `claude`, with the origin
  and, as now, `requested_by`. Claude on Bob's machine can't untag Alice's
  tag, just as it can't untag Bob's.
- The rules hold on replay, so an installation that broke them locally
  (modified code, a hand-edited log) can't break them for everyone else:
  its events have no effect when the others replay them.
- Sync never runs in the container, and the container never sees `sync/`.
  The session sees synced changes the way it sees an ingest: new events in
  read-only feeds.

## The pool

The **pool log** (`pool/logs/`) records membership, replicated like any
other feed:

- `add-member` (an origin's key, a name, its role (`member` or `hub`), the
  addresses where it listens), `bind-user`, `remove-member`,
  `set-addresses`. Each is signed by a member.
- Because membership is replicated, joining through any one member is
  joining the pool. Every member learns of every other, and connects to
  whoever it can reach.

**Joining.** A member makes an invitation: `adhoc sync invite` prints a
code holding the matter ID, a one-time secret and addresses to try (the
hub's, if there is one). On the new machine, `adhoc sync join <code>`
creates an empty matter, connects, proves the secret, and is added by the
member that answered. Then it syncs everything.

**Pushing to a hub.** On a matter with no pool yet, `adhoc sync push
https://hub.example.com` adds the hub as a member and uploads. A hub accepts
a new matter from people its administrator has enrolled (a list of user keys,
or an enrollment token), without the administrator doing anything per
matter. Invitations from then on name the hub, so new members can join while
the original laptop is closed.

**Leaving and removal.** `remove-member` stops every other member from
syncing with that installation. It can't recall what was already copied;
the doc says so plainly, and so does the command.

### Isolation (Principle 2)

Syncing decides who has the matter's documents, so it is an isolation
control, like opening a session:

- **Only the user, on the host,** can create a pool, invite, join, push to
  a hub, or remove a member. No tool in the container can, and the sync
  keys and peer state are never mounted.
- **Claude can observe** sync state (`adhoc status` reports the pool, its
  members and when each last synced), not change it.
- Publishing work product is *not* an isolation control: it shares work
  with installations that already hold the documents. So Claude can
  publish.
- **Ethical screens:** membership is per matter, explicit, signed and
  logged, so "who has had this matter, and since when?" has an answer.

## Transport

- **Mutually authenticated TLS** between members. Each side checks the
  other's key against the pool log. There are no passwords and no shared
  accounts.
- **Discovery:** the addresses in the pool log, local network broadcast,
  and the hub. No public discovery servers by default: they would tell a
  third party which machines share a matter.
- **Relaying:** when two members can't reach each other, they don't need
  to; the logs reach each one through anyone they can reach (usually the
  hub).
- **The exchange:** each side sends its vector per feed, then the events
  the other lacks, then the objects those events name that the other
  lacks. Large originals go in resumable chunks. A hub is the same protocol
  behind an ordinary HTTPS port.
- **Live while open:** members sync on a timer and when a log changes. The
  viewer refreshes from the feeds, and Claude sees new events through
  `idx changes` and `ann log --since`.

*Why not Syncthing itself?* The single-writer logs and immutable objects
would sync fine as Syncthing folders. But Syncthing doesn't know which
objects an event needs, can't check our signatures, can't sync part of a
matter, uses public discovery servers by default, and is another daemon to
install. The pool, membership and authorship rules would be ours either
way, and with them the protocol is small.

## What changes in existing code

- `Matter`: `matter.toml` with the matter ID; `EventLog` per origin; feeds
  that read the union in replay order; HLC event IDs; signing.
- `State.replay`, `Annotations.apply` and `check`: replay order, refusal on
  replay, the conflict cases above.
- Indexes: vector cursors.
- The viewer (`matter.rs`, `protocol.rs`) reads per-origin logs.
- Container mounts: `work/` read-only; `pool/` read-only for `adhoc
  status`; `sync/` never.
- Migration: an existing matter's logs become the logs of the installation
  that holds them. Nothing is rewritten.
- New: `work` (publish, checkout, log), `adhoc sync` (invite, join, push,
  status, conflicts, remove), `adhoc hub serve`.

## Order of building

1. Per-origin logs, HLC IDs, the matter ID and vector cursors, with no
   network. Test by copying logs between two matter directories by hand: two
   installations that exchange logs must end in the same state. *Built;
   tests in [`test_sync.py`](tests/test_sync.py).*
2. Replay rules and `adhoc sync conflicts`. *Built; tests in
   [`test_sync.py`](tests/test_sync.py).*
3. Publishing work product. *Built; tests in [`test_work.py`](tests/test_work.py).*
4. Signing, the pool log, and sync between two members on a LAN.
5. The hub, push and invitations through it.

## Open questions

- **Matter-wide numbers.** Bates numbers and exhibit numbers must not
  repeat across installations. Two people producing at once could assign the
  same range. Productions and exhibit sets would be published work, and a
  number range reserved by an event. With a hub, reservations go through it;
  without one, two concurrent reservations of the same range would be a
  conflict that blocks the later production until resolved.
- **Clawback and destruction.** Append-only logs and replicated objects
  make deletion hard, but the law sometimes requires it: clawing back a
  privileged document produced by mistake, or destroying documents under a
  protective order when the case ends. A `purge` event, honored by every
  member, would delete the objects and blank the content of events that
  name them, keeping a record that it happened.
- **A hub that can't read the documents.** Encrypting what the hub stores,
  with keys only members hold, as Syncthing's "untrusted devices" do. It
  costs the hub any server-side features later. Not in the first version.
- **Partial replicas.** A laptop may not have room for a large matter.
  Every installation would get all logs and text layers, with originals
  fetched by collection or on first view. That trades away some of the
  speed that local first is for, so it's opt-in.
- **Indexes.** Each installation builds its own, and semantic indexing a
  large matter takes a while. A hub could build and offer them, checked
  against their settings like any index.
- **The audit log.** Should a hub collect members' audit logs, for a firm
  that wants one record of every session on a matter?
- **Hotkeys** are matter-wide today. With several people, they might be
  per person.
- **Who can remove whom.** Any member, or only certain people (the matter's
  owner, the hub's administrator)?

## Decided

- Local first: every installation holds the matter and works offline.
- One pool per matter; joining through any member joins the pool.
- An optional hub, which members can push to.
- Synced: documents, annotations (tags, notes, entities), published work
  product, membership. Not synced: the workspace, indexes, `claude-home/`.
- Work product made in the workspace syncs only when published, explicitly.
