# Screenshots

*Status: step 1 is built: screenshots of the viewer, without Claude, taken
by [`tools/screenshots/shoot.py`](../tools/screenshots/shoot.py) from the
shot list [`shots.toml`](../tools/screenshots/shots.toml), locally or in CI
([`.github/workflows/screenshots.yml`](../.github/workflows/screenshots.yml)).
See [Not built yet](#not-built-yet).*

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

In CI, the **Screenshots** workflow does the same on every push to `main`,
on pull requests that touch the viewer, the Python code, the corpus or the
shot list, and on demand (*Run workflow*). The pictures are the run's
`screenshots` artifact.

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
| `click = [x, y]`, `move = [x, y]` | a click, or the pointer moved (for hover cards) |
| `sleep = 0.5` | a pause |

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

## Not built yet

The shots above show the viewer only. Claude Code's terminal beside it
needs a session, and a session needs a Claude login, which CI can't do.
The plan:

- **Record once, replay in CI.** A session is recorded from a scripted
  list of prompts (on a developer's machine, with a real login). In CI,
  the real Claude Code runs against a small local server
  (`ANTHROPIC_BASE_URL`) that replays the recorded model responses in
  order, so its tool calls (`view`, `search`, …) really run against the
  current code and the viewer shows what they do now. Nothing in CI talks
  to Anthropic, and the prompts can be typed into the terminal at an even
  pace.
- **Stale recordings fail loudly.** When a replayed tool call's output
  differs from what was recorded (a command renamed, a count changed), CI
  says which scenario to record again, rather than showing Claude
  describing output that is no longer on the screen.
- **Videos,** with ffmpeg recording the virtual screen and the pacing
  (typing speed, pauses) set by the scenario rather than by how long
  things took.
