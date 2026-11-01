# Syncing a Matter Across Installations

*Status: built, in the five steps under "Order of building", with tests for
each, and since then: hotkeys kept local, Bates and exhibit numbers
reserved before use, people bound to installations, removed members' later
events set aside, syncing as things change, and finding members on the
local network. Decisions still open are at the end.*

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
   with a stable address. Anyone its administrator has enrolled can **push**
   a matter to it, without the administrator pulling it in, and others join
   through it. A hub is a
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
| Hotkeys (`annotations/hotkeys.json`) | no | each person's keyboard shortcuts are their own |
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
  elsewhere: the same tag defined twice, an untag of a tag
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
    and the second's color and description become a conflict to
    look at. Its applications still count, since it's the same name.
  - **Hotkeys aren't synced:** each installation keeps its own, in
    `annotations/hotkeys.json`, so they never clash. (Hotkeys an
    installation set before this, in its tag definitions, stay its own.)
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

- **People belong to installations** ([`pool.py`](src/adhocdocs/pool.py)).
  An event authored `user:alice` (a tag, a note, a publication) counts only
  if it comes from an installation that writes for Alice; from anywhere
  else it has no effect, and is reported. An installation comes to write
  for a person by claiming them (the first time it writes as someone nobody
  has claimed; founding a pool claims everyone who wrote there before), or
  by being vouched for by one of theirs: `adhoc sync vouch <origin> --user
  alice`, or an invitation made with `--same-person`, whose joiner proves
  it holds the invitation's secret (the hub that accepts it sees only the
  secret's hash, so it can't). A person no installation has claimed is
  anyone's to write as.
- **None of it depends on the order of events**, since event IDs are the
  writer's to choose: who writes for whom is worked out from all the claims
  and vouches at once, so a backdated claim takes no one over. Short of
  per-person keys, a member running modified code *can* claim a person
  someone else already claimed. Then both write for them, and the person is
  **disputed**: `adhoc sync conflicts` reports it to everyone, so the
  impersonation is plain to see, and removing the installation that isn't
  theirs settles it. (The write path refuses to do this.)
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

- `add-member` (an origin's certificate, a name, its role (`member` or
  `hub`), the addresses where it listens), `remove-member`, `set-addresses`
  ([`pool.py`](src/adhocdocs/pool.py)). Each is signed by a member.
- **Who counts as a member:** the founder (in `matter.toml`, and in every
  invitation), by its own `add-member`; after that, an event counts only if
  its installation is a member when it's replayed. So everyone agrees on
  the members, and an installation that was removed can't add itself back.
- **Founding** a pool (the first `adhoc sync invite`, or `serve
  --advertise`) seals the installation's existing logs: events written
  before signing existed are signed in place, IDs unchanged. After that the
  logs are shared, and are never rewritten.
- Because membership is replicated, joining through any one member is
  joining the pool. Every member learns of every other, and connects to
  whoever it can reach.

**Joining** ([`invite.py`](src/adhocdocs/invite.py)). A member makes an
invitation: `adhoc sync invite` prints a code holding the matter ID, its
founder, the inviting origin, addresses to try and a one-time secret (kept
on the inviter only as a hash, for three days by default). On the new
machine, `adhoc sync join <code> <dir>` creates an empty matter, connects
(checking the inviter is the origin the code names), proves the secret, and
is added by the inviter, in the name of the person who invited it. Then it
syncs everything.

**Pushing to a hub** ([`hub.py`](src/adhocdocs/hub.py)). The hub's
administrator enrolls people (`adhoc hub enroll --person alice`), which
prints a hub code: the hub's addresses, the hash of its key, and a token.
On a member, `adhoc sync push <code>` founds the pool if there isn't one,
sends the hub the pool's membership log, and the hub (checking the pusher
is a member) makes a replica with an origin of its own. The member adds
the hub to the pool with the role `hub`, and syncs everything to it. No one
does anything per matter on the hub. Revoking a person stops their code;
matters already on the hub are governed by their own pools.

**Invitations through the hub.** When the pool has a hub, `adhoc sync
invite` leaves the invitation there (only its secret's hash) and the code
names the hub, so a new member can join, and get everything, while the
inviting laptop is closed. The hub adds the new member in the inviter's
name.

**Leaving and removal.** `adhoc sync remove` stops every other member from
syncing with that installation, once they've synced the removal. It can't
recall what was already copied; the command says so. The removal records
how much of the installation's logs the remover had, and nothing past that
counts or is accepted, whatever IDs it carries: event IDs are the writer's
to choose, so a removed member could otherwise backdate its way back in.
And what it wrote after that point, if some member took it in before
hearing of the removal, is set aside everywhere: the removals' cutoffs are
in `pool/cutoffs.json`, and every reader of the logs (the matter's state,
annotations, published work, indexes, the viewer) stops each removed
installation's logs there.

**Known limits.** The server keeps the nonces of signed requests in
memory: a request signed in the five minutes before a restart could be
replayed once after it, by someone who had it (which, inside TLS, means
the member who sent it).

### Bates and exhibit numbers

Bates numbers and exhibit numbers must not repeat across installations
([`numbers.py`](src/adhocdocs/numbers.py)). So a production takes the range
it needs before stamping it: it makes the pages, reserves exactly that many
numbers (a `reserve-numbers` event in the annotation feed, which syncs),
then stamps them. Exhibit sets do the same. Without `--start`, a
production or set begins at the next number not reserved on any
installation the matter has heard from. A failed production gives its
numbers back.

- **With a hub**, reserving syncs with it first, to hear of others'
  reservations, and after, to check no one took the same numbers in the
  meantime, so a clash needs two people reserving at the same moment.
- **Only whoever reserved numbers can give them back**, from the same
  installation, and only until they're stamped: a finished production
  marks its reservation used. A reservation is at most a million numbers,
  starting no more than a million past the next free one, so no one can
  push numbering out of reach.
- **Without one**, two installations that produce before syncing can
  reserve the same numbers. Replay keeps the earlier reservation, and
  `adhoc sync conflicts` reports the later (`numbers-overlap`): that
  production needs redoing with new numbers.
- Productions and sets made before reservations existed are reserved the
  next time their series is used, so the other installations learn of
  them.
- In a session, Claude reserves through the annotation API, which syncs
  with the hub on the host.

### Purging documents

Sometimes documents must go for good: clawed back after a production made
in error, or destroyed under a protective order when the case ends
([`purge.py`](src/adhocdocs/purge.py)). `adhoc purge <doc> --user me
--reason "…"` (a person, on the host; never Claude) records a signed
`purge` event, and every installation that has it, the hub included:

- deletes the documents' objects: originals, text layers and their parts,
  metadata, layers made from them in the workspace, for each document and
  its attachments (what a live document still uses stays);
- redacts the annotation events that name them, since tags and notes can
  hold quoted words. Each becomes a **tombstone** keeping only its ID and
  hash, so signed logs still check; a tombstone is accepted only where a
  purge authorizes its hash;
- treats them as gone for good: out of the state, the indexes and the
  viewer; ingesting the same file again doesn't bring them back; sync never
  fetches, sends or accepts their objects again, and an event naming them
  that arrives late is kept as its tombstone.

A record stays: who purged, when, why, and which document IDs.

**What a purge can't reach** is what the matter doesn't track: the
workspace (drafts, exports, productions), Claude's transcripts in
`claude-home`, copies made elsewhere. Each installation gets a notice
(`adhoc purges`, shown when a session opens, and to Claude in `adhoc
status`) listing the workspace files and published work that mention the
documents, and what else to check. `adhoc purges --ack <purge> --user me`
marks it dealt with.

A document whose original another live document shares (the same file
from another custodian) can't be purged alone; `--with-duplicates` purges
them all.

### Isolation (Principle 2)

Syncing decides who has the matter's documents, so it is an isolation
control, like opening a session:

- **Only the user, on the host,** can create a pool, invite, join, push to
  a hub, or remove a member. No tool in the container can, and the sync
  keys and peer state are never mounted.
- **Claude can observe** sync state (`adhoc status` in a session reports
  the pool's members, from `pool/`, mounted read-only), not change it.
- Publishing work product is *not* an isolation control: it shares work
  with installations that already hold the documents. So Claude can
  publish.
- **Ethical screens:** membership is per matter, explicit, signed and
  logged, so "who has had this matter, and since when?" has an answer.

## Transport

Built in [`peer.py`](src/adhocdocs/peer.py), with the signed logs in
[`signing.py`](src/adhocdocs/signing.py).

- **TLS 1.3, pinned to the origin.** Each installation serves with a
  certificate for its origin key. A client doesn't check it against any
  authority: it checks that the server's key hashes to the origin it meant
  to reach. One port serves several matters; the client names the matter by
  SNI.
- **A matter is named by its ID and its pool's founder.** An ID alone isn't
  secret (it's in invitations, and in clear in SNI), so a hub keeps a
  replica per ID and founder: someone enrolled there who founds a pool of
  their own under a matter's ID gets a replica of their own, and doesn't
  take the real one's place.
- **Signed requests.** Every request is signed by the client's origin key,
  over the method, path, time, a nonce, the body and the server's origin, so
  it can't be replayed or redirected. The server answers active members
  only (and `/join`, for an installation with an invitation). There are no
  passwords and no shared accounts.
- **The exchange:** the client asks for the server's counts and log heads
  per feed and origin. For each log, whoever has more sends the rest:
  objects first (only those the other lacks; large originals are streamed),
  then the events. Membership goes first, since it says whose events to
  accept. Every event is checked against its origin's key and the event
  before it.
- **Nothing is read from strangers.** The server checks a request's
  signature header (a member's origin, a fresh time) before reading any
  body, bodies must say their length and stay within limits, and log names
  must be origin IDs, never paths.
- **What arrives is laid out only where it belongs.** A publication's
  name and files come from other installations, so they're checked before
  anything is written: relative, no `..`, nothing hidden, and only under
  `work/`. Publishing reads workspace files without following links, so a
  link in the workspace can't publish a file from elsewhere on the host.
- **A log that doesn't check is refused, alone.** A bad signature, a broken
  chain, or a fork (two copies of an origin's log that differ: the same
  count with different last events, or a next event that doesn't follow) is
  reported under `refused`, and the other logs still sync.
- **Discovery:** the addresses members record in the pool log (`adhoc sync
  serve --advertise`), and the local network
  ([`lan.py`](src/adhocdocs/lan.py)). No public discovery servers: they
  would tell a third party which machines share a matter. An installation
  that serves broadcasts a small packet on the local network every half
  minute: its port, and a tag that changes every ten minutes and names
  neither the matter nor the installation. A watcher that recognises its
  matter's tag connects, checks the certificate is an active member's, and
  syncs with it there. Someone on the network who already knows a matter's
  ID and founder could recognise the tag, which tells them no more than
  TLS's SNI does when members sync on that network. The tag covers the
  announced port, so a packet heard can't be sent again pointing
  elsewhere; a watcher looks at a few packets a minute from any one
  address, probes each address at most every two minutes, and probes on a
  worker with a short queue, so packets from the network can't make it
  open connections at their pace. `--no-lan` turns it off.
- **Relaying:** when two members can't reach each other, they don't need
  to; the logs reach each one through anyone they can reach (usually the
  hub).
- **As soon as something changes** ([`watch.py`](src/adhocdocs/watch.py)).
  A watcher runs beside the matter on the host: `adhoc open` starts one
  when the matter has a pool, `adhoc sync serve` runs one, and `adhoc sync
  watch` runs one alone (for a laptop that doesn't listen). When this
  installation's logs grow, from work here or from events another member
  sent, it syncs with every member that says where it listens; passing on
  what it received is what carries changes between members that can't
  reach each other. And it keeps a long poll open to each member it can
  reach, which answers when that member's logs change, so a laptop hears of
  others' changes within a second or two, usually from the hub. Every five
  minutes it syncs with everyone anyway. `adhoc sync now` syncs once.
  The viewer refreshes from the feeds, and Claude sees new events through
  `idx changes` and `ann log --since`.

*Why not Syncthing itself?* The single-writer logs and immutable objects
would sync fine as Syncthing folders. But Syncthing doesn't know which
objects an event needs, can't check our signatures, can't sync part of a
matter, uses public discovery servers by default, and is another daemon to
install. The pool, membership and authorship rules would be ours either
way, and with them the protocol is small.

## Commands

On the host (the user's; Claude can't run them in a session):

| Command | Does |
|---|---|
| `adhoc sync invite --user me [--address host:port]` | an invitation code (founds the pool the first time) |
| `adhoc sync join <code> <dir>` | join with it, into a new matter directory, and sync |
| `adhoc sync serve [--listen …] [--advertise host:port --user me]` | be reachable by the other members (and sync as things change) |
| `adhoc sync watch` | sync as things change, without listening (a laptop) |
| `adhoc sync now` | sync with every member that says where it listens |
| `adhoc sync push <hub code> --user me` | put the matter on a hub |
| `adhoc sync status \| members \| conflicts` | what's held from whom, who's in the pool, what clashed |
| `adhoc sync remove <origin> --user me` | take a member out of the pool |
| `adhoc sync vouch <origin> --user me` | that member writes for me too (my other machine) |
| `adhoc purge <doc> --user me --reason "…"` | delete documents everywhere, for good |
| `adhoc purges [--ack <purge> --user me]` | purges carried out here, and what they leave to do |
| `adhoc publish <path> --user me`, `adhoc retire <name> --user me` | share work product as the user |
| `adhoc hub init \| enroll \| revoke \| serve \| matters` | run a hub |

In a session: `work ls | publish | checkout | log | retire` (publishing goes
through the annotation API, stamped `claude`), `ann log --cursor`, and
`adhoc status` for the pool's members.

## Order of building

1. Per-origin logs, HLC IDs, the matter ID and vector cursors, with no
   network. Test by copying logs between two matter directories by hand: two
   installations that exchange logs must end in the same state. *Built;
   tests in [`test_sync.py`](tests/test_sync.py).*
2. Replay rules and `adhoc sync conflicts`. *Built; tests in
   [`test_sync.py`](tests/test_sync.py).*
3. Publishing work product. *Built; tests in [`test_work.py`](tests/test_work.py).*
4. Signing, the pool log, and sync between two members on a LAN. *Built;
   tests in [`test_peer.py`](tests/test_peer.py), over TLS on localhost.*
5. The hub, push and invitations through it. *Built; tests in
   [`test_hub.py`](tests/test_hub.py).*

## Open questions

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

## Decided

- Local first: every installation holds the matter and works offline.
- One pool per matter; joining through any member joins the pool.
- An optional hub, which members can push to.
- Synced: documents, annotations (tags, notes, entities), published work
  product, membership. Not synced: the workspace, indexes, `claude-home/`.
- Work product made in the workspace syncs only when published, explicitly.
- Keyboard shortcuts (tag hotkeys) aren't synced.
- Bates and exhibit numbers are reserved, and the reservation synced, before
  a production or exhibit set uses them.
- Documents can be purged everywhere, by a person; what's in the workspace
  and transcripts is left to each installation's user, with a notice.
- Any member can remove any other, for now.
