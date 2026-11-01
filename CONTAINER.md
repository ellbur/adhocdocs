# The Container

*Status: built: [`container/`](container) (the image),
[`container.py`](src/adhocdocs/container.py) (`adhoc open`, `adhoc image
build`, `adhoc status`) and [`proxy.py`](src/adhocdocs/proxy.py). The mounts
are listed in `container.py`'s docstring.*

Claude Code can run any code, so the only way to be sure it sees one
matter's documents and nothing else (Principle 2) is a box holding only that
matter. The box must:

1. **Keep matters apart:** no documents, transcripts or caches cross over.
2. **Feel close to invisible:** `adhoc open smith-v-jones`, and the user is
   talking to Claude inside it.
3. **Follow Anthropic's rules:** each user signs in with their own account,
   and adhocdocs never touches credentials or sits between the user and
   Claude Code.

## What Anthropic's terms require

Our reading on 2026-10-30, not legal advice, of
[Claude Code legal and compliance](https://code.claude.com/docs/en/legal-and-compliance),
[authentication](https://code.claude.com/docs/en/authentication), the
[Commercial Terms](https://www.anthropic.com/legal/commercial-terms) and the
[Consumer Terms](https://www.anthropic.com/legal/consumer-terms):

- **Sign-in goes through Anthropic's own flow.** Developers "may not collect,
  store, or intermediate Claude.ai credentials or session tokens", nor offer
  Claude.ai login in their own apps. An end user may sign in to "the
  unmodified Claude Code binary with their own Claude subscription".
- **Users' own API keys are fine,** including keys configured "in a
  development environment, secrets manager, or machine image for use by the
  customer's own authorized users".
- **Running Claude Code inside a product:** the binary "must not be
  modified", must be "installed and run as published", and we may not
  "remove, disable, or restrict any authentication method". There's no
  paying for, reselling or intermediating usage. adhocdocs runs locally as
  open source, so it may not be a "product or service", but we design as if
  it were.
- **No automated use on subscriptions** (Consumer Terms §3.7). Claude writing
  and running scripts in a session is ordinary use. Driving Claude Code from
  a script (`claude -p` in a loop) needs an API key.
- **Branding:** we can say adhocdocs "runs Claude Code", but "Claude" and
  "Anthropic" can't be in its name or logo.
- **Confidentiality, for users to know:** Free, Pro and Max fall under the
  Consumer Terms, which allow training unless the user opts out. Team,
  Enterprise and API fall under the Commercial Terms, with no training on
  customer content. adhocdocs says this plainly at setup and recommends
  nothing.

This rules out: copying the host's credentials into the container, passing a
`setup-token` as `CLAUDE_CODE_OAUTH_TOKEN`, our own login screen or relay
chat window, a patched or wrapped binary or settings that hide login
options, publishing an image with Claude Code baked in, and scripted driving
on a subscription.

## Design

```
 user's machine
 ┌──────────────────────────────────────────────────────────────────┐
 │ adhoc (launcher)    proxy, viewer, ann API,   <matter>/          │
 │                     audit API                 audit/   (host)  │
 │   │ starts, attaches        ▲ Unix sockets       store/   (ro)   │
 │   ▼                         │                    annotations/(ro)│
 │ ┌─────────────────────────────────────────┐      workspace/ (rw) │
 │ │ container: one matter, one session      │      claude-home/    │
 │ │   claude (official, unmodified)         │                      │
 │ │   adhocdocs CLIs: search, idx, view, …  │                      │
 │ │   network: none (proxy: Anthropic only) │                      │
 │ └─────────────────────────────────────────┘                      │
 └──────────────────────────────────────────────────────────────────┘
```

- **One container per session, one matter per container.** Starting it is
  Principle 2's connect operation, done by the user on the host.
- **The terminal is attached straight to `claude`**, as with `podman run
  -it`; nothing reads or relays it.
- **The image is built on the user's machine** from our Containerfile, which
  installs Claude Code with Anthropic's official installer. Claude Code runs
  as published, with normal auto-update.
- **Claude learns the tools from a managed `CLAUDE.md`**
  ([`container/CLAUDE.md`](container/CLAUDE.md), installed at
  `/etc/claude-code/CLAUDE.md`). It's kept short, and points at `--help`.

**Mounts:** the store and annotation log read-only (originals can't change,
and annotation writes go through the API that stamps authorship); the
workspace read-write; the matter's `claude-home/` as `~/.claude`; and the
viewer and annotation API sockets (the launcher runs `adhoc view` and
`adhoc ann-api`, and sets `ADHOC_VIEW_SOCKET` and `ADHOC_ANN_SOCKET`); and
the audit API's socket, read-only, alone in its own directory. There is nothing else: no host home, no other
matter, no Podman socket.

**Network: none, plus a proxy on the host.** The session runs with
`--network none`, so the kernel gives it a loopback interface and nothing
else: no route out, no DNS. The launcher runs an allowlist proxy on the host,
listening on a Unix socket in the session's socket directory, and a relay in
the session forwards `127.0.0.1:3128` to it for Claude Code's `HTTPS_PROXY`.
The proxy passes TLS through to allowed host names without decrypting it,
refuses everything else, and logs each decision to `<matter>/run/proxy.log`.
Claude can kill the relay, which only cuts the session off, and can't
change the allowlist, which lives on the host.

The allowlist is the hosts Claude Code needs to sign in, call the API and
update itself (from its
[network docs](https://code.claude.com/docs/en/network-config#network-access-requirements)):
`api.anthropic.com`, `claude.ai`, `claude.com`, `platform.claude.com`,
`sso.claude.com`, `downloads.claude.ai`. Telemetry, plugin registries,
GitHub, claude.ai connectors (`mcp-proxy.anthropic.com`) and artifacts are
not on it. Some features go through `api.anthropic.com` itself, which the
network can't tell apart: web search and fetch, connectors, artifacts. The
image's managed settings turn those off. That second layer is enforced by
Claude Code, not by the model. It doesn't touch sign-in methods.

*Earlier plan:* an `--internal` Podman network with the proxy in its own
container on both networks. `--network none` is simpler and needs no
network kernel modules, and the proxy joins the viewer and annotation API as
a host service on a socket.

**Sign-in:** inside the container, through Anthropic's flow. Claude Code
shows a URL, the user signs in on the host and pastes back the code. With
`claude-home/` per matter, this happens **once per matter**. adhocdocs never
reads, copies or sends what Claude Code writes there. It only provides the
directory, as any computer provides a home. That also keeps transcripts
(which contain document text) with their matter, and `claude --resume` works
within a matter. This is the one interpretation the design depends on. If
Anthropic reads "store" to cover it, the fallback is a `claude-home/` that
lives only as long as the container (sign in every session); nothing else
changes. One home per machine was rejected: it carries transcripts between
matters.

**API keys** work too: set the normal Claude Code way, and passed through by
the launcher, never saved.

**Multiple sessions:** two containers can run at once, each with its own
viewer instance. Two on one matter share `claude-home/`, like two `claude`
processes on one machine.

**Podman, rootless, everywhere:** no root daemon, so an escape gets only the
user's rights. It is free where Docker Desktop needs licenses at larger
firms, and the files and options are the same as Docker's. On macOS and
Windows it runs in `podman machine`. The viewer stays native and reads the
matter store on the host directly, not through the VM.

**Tested** on Linux with Podman 6.1 (rootless):
- The store and annotations are read-only in the session; the workspace and `claude-home` are writable.
- The session has only a loopback interface, no DNS, and no direct route out.
- The proxy refuses `example.com` and passes `api.anthropic.com`.
- `claude` reaches the API through the proxy and keeps its config in the matter's `claude-home`.
- `ann` writes are recorded as `claude`, and writing the log directly fails.
- `search`, `idx`, semantic search (offline, model in the image) and `view` all work from inside.

Signing in has to be done by a person, so it hasn't been tested yet.

**Not built yet:**
- macOS and Windows: Unix sockets don't cross `podman machine`'s file sharing. The way in is designed and built, but not yet tried on either: a bridge container in the VM, and a link on the host that dials into it ([WINDOWS.md](WINDOWS.md)).
- Keeping Claude Code current: its auto-update writes into the container, which is discarded after each session. `adhoc setup --no-cache` (or `adhoc image build --no-cache`) gets the latest.

**Audit log:** the host records each session in `<matter>/audit/`, which is
never mounted ([`audit.py`](src/adhocdocs/audit.py); `adhoc audit sessions |
show | verify`). Three host processes write it:
- The launcher records the session's start (who opened it, image ID, `podman run` arguments) and its end (exit status).
- The proxy records each connection, allowed or refused, with the bytes each way.
- The audit API records what Claude Code's hooks send it: every prompt, and every tool call before it runs and after.

The hooks are in the image's managed settings, with `allowManagedHooksOnly`,
so Claude can't remove them. Before a prompt or tool call they fail closed:
if the record isn't written, the call doesn't run. Lines are hash-chained, so
`verify` finds a line changed or removed afterwards. Whole tool results are
kept beside the log, by hash.

Two limits of the record:
- Anything in the session can post to the audit socket, so a hook record shows something was reported, not that it happened. What the log shows is that nothing Claude Code ran is missing from it.
- The audit socket is alone in a read-only mount, so nothing in the session can put its own listener in its place.

Tested with Claude Code 2.1.316: the managed hooks fire for session start,
prompts and session end. Tool calls need a signed-in session, which hasn't
been tested yet.

*Why hooks rather than reading the terminal:* the terminal goes straight to
`claude`, and reading it would put adhocdocs between the user and Claude
Code. Hooks are Claude Code's own published mechanism for this.

**Observable, not changeable:** `adhoc status` in the container reports the
matter, mounts, read-only state, allowlist and viewer status, and nothing
about credentials.

## Decided

- Sign in once per matter, with per-matter `claude-home/`; never once per
  machine.
- Inform about account types, recommend nothing.
- Podman, rootless.
- No network in the session; a host-name-filtering pass-through proxy on the
  host, reached over a socket.
- Telemetry, connectors, artifacts, web search and fetch turned off by the
  image's managed settings.
- An audit log on the host, fed by managed hooks that fail closed.
- Check our reading with Anthropic if the project goes anywhere; until then
  build as designed, with sign-in-per-session as the fallback.
