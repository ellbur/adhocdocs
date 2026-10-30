# The Container

*Status: draft. Nothing here is decided yet; see [Decisions to make](#decisions-to-make).*

## The problem, in plain terms

Claude Code can run any code it likes. So the only way to be sure it sees
one matter's documents and nothing else (Principle 2) is to run it in a
box that holds only that matter's documents. That box is the container.

The container has to do three things at once:

1. **Keep matters apart.** Nothing from matter A is reachable while working
   on matter B: not documents, not old transcripts, not caches.
2. **Feel close to invisible.** The user types `adhoc open smith-v-jones`
   and is talking to Claude, inside the box, a moment later.
3. **Follow Anthropic's rules.** Each user signs in with their own Claude
   account, and adhocdocs never touches those credentials or sits between
   the user and Claude Code.

The third point decides more of the design than it first seems to, so it
comes first.

## What Anthropic's terms require

Sources, read on 2026-10-30:
[Claude Code legal and compliance](https://code.claude.com/docs/en/legal-and-compliance)
(and the [authentication](https://code.claude.com/docs/en/authentication)
page it links), the
[Commercial Terms](https://www.anthropic.com/legal/commercial-terms), and
the [Consumer Terms](https://www.anthropic.com/legal/consumer-terms), which
govern Free, Pro and Max plans. This is our reading, not legal advice.

### Sign-in

- **Sign-in must go through Anthropic's own flow.** The legal page: "developers
  may not collect, store, or intermediate Claude.ai credentials or session
  tokens — sign-in to a Claude account must complete through Anthropic's own
  flow." And: third-party developers may not "offer Claude.ai login into their
  own applications, or … route requests through Free, Pro, or Max plan
  credentials on behalf of their users."
- **Signing in to the real binary is fine.** The same section says it does not
  "prevent an end user from signing in to the unmodified Claude Code binary
  with their own Claude subscription".
- **Users' own API keys are fine** when the usage is billed to the key's owner
  and not resold or intermediated. The page explicitly allows "configuring an
  API key in a development environment, secrets manager, or machine image for
  use by the customer's own authorized users".
- **No account sharing.** Consumer Terms §2: "You may not share your Account
  login information … with anyone else."

### Running Claude Code inside another product

The legal page has conditions for "preinstalling or running Claude Code in
your products or services (e.g. in hosted sandboxes or other agent
infrastructure)":

- "The Claude Code binary must not be modified." It must be "installed and
  run as published by Anthropic", and we "may not remove, disable, or restrict
  any authentication method built into it".
- "Customers may not pay for, resell, or intermediate Claude usage on their
  end users' behalf. Each end user must authenticate with their own …
  credentials."
- These conditions come with "agreeing to our Commercial Terms of Service".

adhocdocs is open-source software the user runs on their own machine, not a
hosted service, so it may fall outside "your products or services". We
design as if the conditions apply anyway. They cost little, and they keep us
clear either way.

### Automated use

- Consumer Terms §3.7 forbids accessing the services "through automated or
  non-human means, whether through a bot, script, or otherwise", except "via
  an Anthropic API Key or where we otherwise explicitly permit it".
- The legal page: Pro and Max usage limits "assume ordinary, individual usage
  of Claude Code and the Agent SDK".

Claude writing and running scripts inside the session (Principle 3) is
ordinary Claude Code use. What adhocdocs must not do is drive Claude Code
itself from a script, for example by running `claude -p` in a loop over
documents on a subscription login. If a tool ever needs that, it requires an
API key.

### Branding

We can say adhocdocs "runs Claude Code". We can't put "Claude" or
"Anthropic" in the product's name or logo, or suggest Anthropic endorses it.

### Confidential data

This isn't a rule for us, but users need to know it, and evidentiary
documents are usually confidential:

- **Free, Pro and Max** are under the Consumer Terms. Conversations may be
  used for training unless the user opts out, and even after opting out in
  some cases (Consumer Terms §4).
- **Team, Enterprise and API** are under the Commercial Terms: no training on
  customer content, and the content is the customer's confidential
  information (Commercial Terms B and E.1). Zero Data Retention is available
  for some organizations.

adhocdocs can't enforce which kind of account is used. It can say this
plainly at setup, and leave the choice to the user or their firm.

### What this rules out

| Idea | Why not |
|---|---|
| Copy the host's `~/.claude/.credentials.json` (or keychain entry) into the container | Collecting and storing credentials. It would also bring the host's other projects into the box. |
| Generate a token with `claude setup-token` and pass it in as `CLAUDE_CODE_OAUTH_TOKEN` | adhocdocs would be storing and passing a subscription token. It's also meant for scripts, which raises §3.7. |
| Our own login screen, or a chat window that relays to Claude Code | Offering Claude.ai login in our app, and intermediating. This is the "proxying" worry in [DESIGN.md](DESIGN.md#open-questions). |
| A patched or wrapped `claude` binary, or a settings file that hides the login options | Modifying the binary, or restricting its auth methods. |
| A prebuilt image with Claude Code baked in, published by us | Redistributing the binary. It's safer to install it from Anthropic's official installer when the image is built on the user's machine. |
| Driving Claude Code from a script on a subscription login | §3.7, unless the user supplies an API key. |

## Proposed design

### The shape

```
 user's machine
 ┌────────────────────────────────────────────────────────────────────┐
 │ adhoc (host launcher)        viewer (host)       matter store      │
 │   │ starts, attaches          ▲                    objects/  (ro)  │
 │   ▼                           │ view API           annotations     │
 │ ┌───────────────────────────────────────────┐      workspace/ (rw) │
 │ │ container: one matter, one session        │      claude-home/    │
 │ │   claude (official, unmodified)           │                      │
 │ │   adhocdocs CLIs: view, ann, page, …      │                      │
 │ │   network: Anthropic only                 │                      │
 │ └───────────────────────────────────────────┘                      │
 └────────────────────────────────────────────────────────────────────┘
```

- **One container per session, one matter per container.** Starting a
  session for a matter is the connect operation of Principle 2. The user does
  it on the host with `adhoc`, and Claude can't.
- **The user's terminal is attached straight to `claude`** in the container,
  the same as `docker run -it`. adhocdocs doesn't read, rewrite or relay what
  passes through it. That's what keeps this a terminal and not a proxy.
- **The image is built on the user's machine.** The Dockerfile installs Claude
  Code with Anthropic's official installer, plus the adhocdocs tools. We
  publish the Dockerfile, not an image containing Claude Code.
- **Claude Code runs as published**, with its normal auto-update. We don't
  ship settings that hide login methods.

### What the container can reach

| Mount | Access | Why |
|---|---|---|
| matter object store | read-only | Originals can't be changed from inside (TOOLS.md, *Read-only originals*) |
| annotation log | read-only | Writes go through the API, which stamps authorship ([ANNOTATIONS.md](ANNOTATIONS.md)) |
| matter workspace | read-write | Pages, lists, scripts, derived layers |
| matter's Claude home (`~/.claude`) | read-write | See [Signing in](#signing-in) |
| viewer and annotation API sockets | as provided | The only way out to the host |

There is nothing else: no host home directory, no other matter, no Docker
socket.

**Network:** outbound traffic is allowed only to what Claude Code needs
(the Anthropic API, and the sign-in and update hosts), through an allowlist
the launcher sets up. Without this, Claude could send documents anywhere,
which Principle 2 doesn't allow. The exact host list needs checking against
Claude Code's network documentation when we build it.

### Signing in

Signing in happens inside the container, by the unmodified `claude`, through
Anthropic's flow. There's no browser in the container, so Claude Code shows
a URL. The user opens it on the host, signs in, and pastes back the code.
adhocdocs is not involved.

The open question is how often the user has to do this. Three options:

**A. Sign in every session.** The container's `~/.claude` lives only as long
as the container. *Clearly allowed*, and nothing about a session outlives it.
But it costs a browser round-trip every time, which works against
"invisible", and it loses `claude --resume`.

**B. Sign in once per matter** *(recommended)*. Each matter has a
`claude-home/` directory next to its documents, mounted as the container's
`~/.claude`. Claude Code writes its own credentials there, as it would on any
machine, and they last until the user signs out or the matter is closed.
- adhocdocs never reads, copies or sends that file. It only provides the
  directory, the same way a home directory is provided on any computer.
- This is better for isolation too. `~/.claude` also holds transcripts, and
  transcripts contain document text, so they belong with the matter anyway:
  kept with it, never visible to another matter, deleted with it.
  `claude --resume` works within a matter.
- The risk is that "store" in "collect, store, or intermediate" might be read
  to cover providing that directory. We think it doesn't: the credentials are
  written by Claude Code, into the user's own storage, and are never handled
  by our code. But this is the one interpretation the design depends on. The
  legal page says to "contact sales" with questions about allowed
  authentication. If the answer is no, we fall back to A, and the rest of the
  design is unchanged.

**C. Sign in once per machine.** One `~/.claude` is shared by every matter.
*Rejected:* it carries transcripts and memory from one matter into the next.
Mounting only the credential file avoids that, but means our launcher picking
out and handling the credential file, which is the thing we must not do.

**API keys** work with any of these. A user (or their firm) who has an API key
sets it up the normal Claude Code way, for example `ANTHROPIC_API_KEY` in
their own environment, or an `apiKeyHelper` they configure. The legal page
explicitly allows this. The launcher passes the variable through and never
saves it.

### Multiple sessions

Two containers can run at once, for two matters, or for two sessions on one
matter. Each has its own viewer instance (VIEWER.md: one instance per
session). With option B, two sessions on one matter share `claude-home/`,
which is the same as running two `claude` processes on one machine.

### Host platforms

- **Linux:** Docker or Podman. Rootless Podman is preferred where available.
- **macOS and Windows:** containers need a Linux VM (Docker Desktop, Podman
  machine, Colima, WSL2). The viewer still runs natively on the host. File
  sharing into the VM is slower, which matters for the 100 ms viewer target
  only if the viewer reads through the VM. It shouldn't: the viewer reads the
  matter store on the host directly.

### What Claude can see

Per Principle 2, Claude can observe this state but not change it. An
`adhoc status` command inside the container reports the matter, the mounts,
whether they're read-only, the network allowlist, and the viewer's status.
It doesn't report anything about credentials. Claude Code's own `/status`
already shows which account is in use.

## Decisions to make

1. **Sign-in persistence:** A (every session), or B (once per matter, which
   depends on the reading above). Possibly start with A and move to B after
   asking Anthropic.
2. **Should we ask Anthropic** about B (and about whether a locally run open
   source launcher counts as "your products or services") before building?
3. **Account guidance:** do we just inform users about Consumer vs.
   Commercial terms for confidential documents, or recommend (or require) a
   Team, Enterprise or API account?
4. **Runtime:** Docker and Podman both, or just one to start with?
5. **Network allowlist:** enforced by the container runtime's network
   settings, or by a small egress proxy the launcher runs. A proxy for
   *network traffic* is not the same as proxying Claude Code, but it should
   only filter by host, never read or change content.
