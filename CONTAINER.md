# The Container

*Status: decided, not built. The matter layout the tools use is in
[`matter.py`](src/adhocdocs/matter.py).*

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
 │ adhoc (launcher)          viewer (host)        <matter>/         │
 │   │ starts, attaches        ▲ view API           store/   (ro)   │
 │   ▼                         │                    annotations/(ro)│
 │ ┌─────────────────────────────────────────┐      workspace/ (rw) │
 │ │ container: one matter, one session      │      claude-home/    │
 │ │   claude (official, unmodified)         │                      │
 │ │   adhocdocs CLIs: search, idx, view, …  │                      │
 │ │   network: Anthropic only               │                      │
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

**Mounts:** the store and annotation log read-only (originals can't change,
and annotation writes go through the API that stamps authorship); the
workspace read-write; the matter's `claude-home/` as `~/.claude`; and the
viewer and annotation API sockets. There is nothing else: no host home, no
other matter, no Podman socket.

**Network:** an `--internal` Podman network with no route out, plus a small
proxy container on both networks that passes TLS through to allowlisted
hosts by name, never decrypting. `HTTPS_PROXY` points at it; bypassing it
fails because there is no route. The allowlist is set by the launcher on
the host. The exact host list must be checked against Claude Code's network
docs when building.

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

**Observable, not changeable:** `adhoc status` in the container reports the
matter, mounts, read-only state, allowlist and viewer status, and nothing
about credentials.

## Decided

- Sign in once per matter, with per-matter `claude-home/`; never once per
  machine.
- Inform about account types, recommend nothing.
- Podman, rootless.
- Internal network plus a host-name-filtering pass-through proxy.
- Check our reading with Anthropic if the project goes anywhere; until then
  build as designed, with sign-in-per-session as the fallback.
