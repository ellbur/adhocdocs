# Screenshots

*Status: built: still shots of the viewer
([`shoot.py`](../tools/screenshots/shoot.py), from
[`shots.toml`](../tools/screenshots/shots.toml)), and scenarios with Claude
Code beside it, replayed from recordings with no sign-in
([`scenario.py`](../tools/screenshots/scenario.py),
[`fakeapi.py`](../tools/screenshots/fakeapi.py), from
[`scenarios/`](../tools/screenshots/scenarios)), locally or in CI
([`.github/workflows/screenshots.yml`](../.github/workflows/screenshots.yml)).
Recording them is built too
([`recorder.py`](../tools/screenshots/recorder.py), and the **Record
sessions** workflow,
[`record-sessions.yml`](../.github/workflows/record-sessions.yml)).*

Screenshots for documentation should show the current version of the
software, and show it the same way every time, so that a picture changes
only when what it shows has changed.

## Taking them

On Ubuntu 24.04, with the packages [VIEWER.md](VIEWER.md#checking-it-without-a-screen)
lists for checking the viewer without a screen, plus `tesseract-ocr`,
`tesseract-ocr-eng`, `jq`, `fonts-dejavu-core` and `fonts-liberation`:

```sh
cd viewer && cargo build --release && cd ..
uv run python tools/screenshots/shoot.py --out screenshots
```

This makes the sample matter from `corpus/` (about 30 seconds), takes each
shot (about 5 seconds each), and writes `screenshots/NAME.png`, an
`index.html` to look through them, and `manifest.json` with each picture's
hash. `--only NAME …` takes some of them. `--matter DIR` keeps the sample
matter in DIR and reuses it next time.

In CI, the **Screenshots** workflow takes the stills and replays every
scenario on pushes and pull requests that touch the viewer, the Python
code, the container, the corpus or `tools/screenshots/`, and on demand
(*Run workflow*). The pictures and videos are the run's `screenshots`
artifact.

## Writing a shot

A shot is a table in `shots.toml`: a name, a caption, and steps that bring
the viewer to what the picture shows. Steps drive it the way Claude does,
with `view` commands, and the way a user does, with keys and clicks:

```toml
[[shot]]
name = "highlights"
caption = "Passages Claude pointed out, highlighted and labelled."
steps = [
  { sh = "search '\"delegation clause\"' --by hit --limit 4 | tail -n +2 | jq -c .loc > hits" },
  { sh = "view open \"$(head -1 hits)\" --new-pane --mode page" },
  { sh = "while read -r l; do view highlight \"$l\" --label 'Delegation clause'; done < hits" },
  { key = "bracketright" },
]
```

| Step | Does |
|---|---|
| `view = ["open", "{doc:…}", …]` | a `view` command, with these arguments |
| `sh = "…"` | a shell command, with the matter's tools on `PATH` and `ADHOC_MATTER` set, in a directory of the shot's own |
| `key = "ctrl+f Down"` | keys, as xdotool names them |
| `type = "text"` | text typed into whatever has focus |
| `click = TARGET` | a click there (`double = true` beside it: a double click) |
| `hover = TARGET` | the pointer there, then a pause for a hover card (`wait = 1.5` beside it) |
| `wheel = 3` | scrolling at the pointer, in notches (negative: up) |
| `pause = 0.5` | a pause |
| `prompt = "…"` | (scenarios) typed at Claude's prompt, then Enter; waits for the answer |
| `shot = "name"` | (scenarios) a still, now (`caption = "…"` beside it) |

A `TARGET` is `[x, y]` on the screen, or what is there:
`{ text = "Mar 3, 2026", pane = "p3" }`, `{ selector = ".pane-head" }`, or
both (the text inside those elements), with `nth` (counting from 0, or from
the end when negative), `scroll = true` to bring it into view first, and
`at = [fx, fy]` for a point inside its box other than the middle. The
viewer answers where it is (the `locate` command, only in a viewer started
with `--test-hooks`), so a step keeps working when the layout moves.

`{doc:COLLECTION/FILE}` is a document's locator, by its path under
`corpus/` (`{doc:public/scotus-coinbase-v-suski-2024.pdf}`); the file name
alone will do when only one document has it. `theme = "dark"` takes the
shot in the dark theme.

## The same pixels every time

Two runs on the same machine give identical pictures, byte for byte, and
`manifest.json` holds each one's hash so that can be checked. What makes
it so:

- **Nothing in the picture depends on when or where it was taken.**
  Document IDs are content hashes, so the sample matter is the same each
  time it is made. The overview is headed with the matter's directory
  name, so that is fixed too (`sample-matter`, `[matter] name` in the shot
  list).
- **A fixed screen and environment:** Xvfb at the shot list's size and 96
  DPI; the time zone UTC and the locale C.UTF-8; GTK's Adwaita theme with
  no animations and no blinking caret; only the DejaVu and Liberation
  fonts ([`fonts.conf`](../tools/screenshots/fonts.conf)), whatever else
  the machine has.
- **Each shot starts from nothing:** a new viewer with an empty home
  directory, so nothing in one shot's local storage (a theme, a setting)
  carries over to the next. The pointer is parked in the bottom right
  corner, where hovering changes nothing.
- **It waits for the screen, not for a time:** after each step, until the
  screen has stopped changing; and before the picture, it makes the web
  view paint everything once more (by resizing its window a pixel and
  back) and waits again. Without that last repaint, the pane's rounded
  corners came out a few pixels different in about one run in three,
  depending on which partial repaints had happened.
- **No timestamps in the files:** pictures are saved without PNG date
  chunks, so equal screens give equal files.

A different machine, or a newer WebKitGTK, can still draw some pixels
differently. CI's runner is fixed at Ubuntu 24.04, so its pictures are
the reference.

## Scenarios: Claude Code beside the viewer

A scenario shows a session: someone asks Claude for something, Claude
answers and works the viewer, and they look at what it shows. Claude Code
needs a sign-in and gives a different answer each time, so a scenario is
**recorded once and replayed**:

- **The recording** (`scenarios/NAME.cassette.json`) holds what the model
  answered, turn by turn, and what each tool printed when it was made.
- **The replay** runs the real Claude Code, in the real session container
  (`adhoc open`), pointed at [`fakeapi.py`](../tools/screenshots/fakeapi.py)
  instead of the Claude API. It answers each request with the next
  recorded turn, streamed at a pace set for watching (`[pace]` in the
  scenario). Claude Code's tool calls (`search`, `view`, …) really run,
  against the code as it is now, so the viewer shows what they do today.
  Nothing talks to Anthropic, and nothing needs a secret.
- **Stale recordings fail.** Before answering, the replay compares what
  Claude Code sends with the recording: the prompt typed, and each tool's
  output (times, temporary paths and IDs that carry a time aside). Any
  difference means the software changed under the recording, so Claude's
  recorded words may describe something no longer on screen: the scenario
  fails, and its `NAME.report.json` says where. Record it again.

```sh
uv run python tools/screenshots/scenario.py replay [NAME …] --out screenshots
```

writes `NAME.mp4` (the whole session, pointer included), `NAME-SHOT.png`
for each `shot` step, and `NAME.report.json`; on a failure, also
`NAME.failed.png`, what the screen showed. It needs Podman and the session
image built with the Claude Code version the recordings were made with
([`claude-code-version`](../tools/screenshots/claude-code-version)):

```sh
podman build --build-arg CLAUDE_CODE_VERSION="$(cat tools/screenshots/claude-code-version)" \
  -f container/Containerfile -t localhost/adhocdocs:latest .
```

`--direct` runs the `claude` on this machine in the viewer's terminal
instead of the container: quicker for working on a scenario, but not what
users run. `--fast` drops the human pacing, for a quick check.

A scenario file is its steps (above) with `prompt` steps among them:

```toml
caption = "Asking Claude for documents on a topic: a list beside the chat."

[pace]
think = 1.2        # seconds before an answer starts
cps = 180          # characters a second, for Claude's text
typing_ms = 45     # between keys, typing a prompt

[[step]]
prompt = "Which documents mention the forklift recertification? Show them to me as a list."

[[step]]
click = { text = "Dock 4 forklift recertification", pane = "p2" }

[[step]]
hover = { text = "Mar 3, 2026", pane = "p3" }
wait = 1.5

[[step]]
shot = "date-card"
```

**How a scenario's session is set up**, the same for recording and replay:

- **A fresh copy of the sample matter**, so what one scenario writes (lists,
  tags) can't change what another's tools print. Its indexes are built
  before the session starts, rather than by the session in the background,
  whose timing would show in search results.
- **Claude Code past its first run**: its settings in the matter's
  `claude-home/` say the welcome, the theme, the API key and the workspace
  trust are done.
- **Its tools allowed, nothing asked** (`--permission-mode dontAsk`, with
  allow rules). Auto mode, Claude Code's default, checks each tool call on
  Anthropic's side, which can't happen through a recorder or a replay.
- **The API through a socket on the host** (`adhoc open --api-socket`,
  [CONTAINER.md](CONTAINER.md)): the session holds no credential.
- **Typed like a person**: prompts at an even typing pace, the pointer
  gliding along a gentle curve and pausing before a click. (Still shots
  jump the pointer instead, so nothing it passes over can leave a hover
  card behind.) The terminal's cursor doesn't blink under `--test-hooks`,
  so the screen can settle.

The video of a replay isn't the same byte for byte from run to run
(encoder timing, Claude Code's own "Worked for 6s"), but what happens in
it, and when, is.

## Recording

A recording is the same session as a replay, with
[`recorder.py`](../tools/screenshots/recorder.py) in fakeapi.py's place: it
forwards Claude Code's requests to the Claude API, streams the answers
back, and keeps them, with the prompt and tool output that led to each.
The session still holds no credential; the recorder, on the host, does.

**In CI** (the usual way): *Actions → Record sessions → Run workflow*,
optionally naming scenarios (blank: all of them). The job waits for
someone to approve the `recording` environment, then signs in with
Workload Identity Federation: GitHub's signed token for this repository's
`recording` environment, exchanged for a Claude API token that lasts
minutes. No key is stored anywhere, and no other workflow, branch or fork
can get one (the federation rule matches that environment only). It
records, replays what it recorded as a check, and opens a pull request with
the new recordings; the videos are in the run's `recorded-sessions`
artifact. Watch them and read what Claude said before merging.

Record again when the Screenshots workflow says a scenario is stale, when
you add a scenario, and when you move to another Claude Code version
([`claude-code-version`](../tools/screenshots/claude-code-version)).

Setting it up, once:

- In the Claude Console, a federation rule for GitHub Actions whose
  subject is this repository's `recording` environment
  (`repo:OWNER@ID/REPO@ID:environment:recording`: GitHub's subject carries
  the owner's and repository's numeric IDs). Its IDs go in the workflow's
  `env`; they aren't secrets.
- In GitHub, the `recording` environment, with required reviewers.
- In GitHub, Settings → Actions → General → Workflow permissions: *Allow
  GitHub Actions to create and approve pull requests*.
- The workflow on the default branch (GitHub offers *Run workflow* only
  for those).

**Locally**: `scenario.py record NAME` with `ANTHROPIC_API_KEY` set (or
the federation variables and `ANTHROPIC_IDENTITY_TOKEN_FILE`); it writes
`scenarios/NAME.cassette.json`. Recordings made in CI are the reference,
since tool output (paths in particular) is what replays are checked
against.

**What a recording holds**: the model's answers (text, tool calls, and
thinking blocks, whose text Claude Code doesn't show), the prompts, and what
each tool printed. Not the requests themselves: no system prompt, no
credential.

**One beta is left out.** In a recording, Claude Code believes it has an
API key and sends the betas it sends with one, among them
`claude-code-20250219`. With a federated token, a request carrying that
beta is refused, "Your credit balance is too low to access the Anthropic
API", though the organization has credit; every other beta goes through
(`anthropic-wif-test.yml` tries each alone). So the recorder sends federated
requests without it, and with the OAuth beta that such a token goes with.
Claude Code signing in with federation itself doesn't meet this.

`anthropic-wif-test.yml` checked that federation works for a job in the
`recording` environment and that Claude Code signs in with it; it can go
once a recording has been made.

## Not built yet

- **Editing the video**: speeding up long waits (OCR, a slow search)
  from the timestamps the replay logs, and marking clicks.
