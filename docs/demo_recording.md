# Recording the launch demo GIF

**Status: `unverified` — researched and scripted this session, never installed or run.**
Nothing below has produced an actual GIF yet. Treat every claim here as "should work per the
tool's own docs," not as demonstrated.

## Recommended toolchain: VHS (charmbracelet/vhs)

[VHS](https://github.com/charmbracelet/vhs) drives a real terminal from a scripted `.tape` file
and renders the recording straight to a GIF — no separate "record then convert" step, and the
recording is scripted (reproducible, re-runnable after this demo drifts) rather than a live
human capture.

**Install** (from VHS's own README):

```bash
# macOS / Linux, via Homebrew
brew install vhs

# Any platform with Go installed
go install github.com/charmbracelet/vhs@latest
```

VHS also needs `ffmpeg` on `PATH` (and `ttyd` on macOS/Linux — VHS's installer pulls it in as a
dependency; check VHS's own install docs for the current requirement on your platform, since
this hasn't been verified here).

**Known caveat, stated rather than glossed over:** VHS's PowerShell support has a history of
real bugs (see [charmbracelet/vhs#289](https://github.com/charmbracelet/vhs/issues/289) — a
`Set Shell powershell` reproduction that didn't execute the tape's commands correctly; the
linked fix's merge status wasn't confirmed by this research). Since this repo already has a
bash form of the demo script (`demo_scenario.sh`, `runtime-verified` end-to-end per Gate C /
Session 040), **the tape below targets `bash`, not PowerShell** — the safer, better-documented
path, avoiding an open compatibility question entirely. On Windows this means running VHS from
Git Bash or WSL, not from a native PowerShell prompt.

## The tape script

Save as `docs/demo.tape` (not committed as a build artifact — this is the input, not the
output):

```text
Output docs/demo.gif

Set Shell bash
Set FontSize 16
Set Width 1200
Set Height 700
Set Theme "Dracula"

Type "bash demo_scenario.sh"
Enter
Sleep 15s
```

`Sleep 15s` is a placeholder — `demo_scenario.sh` runs a real `pytest -q` and a real hook
subprocess invocation; time it once manually and adjust the sleep to just past that, so the
recording doesn't cut off mid-command or sit on a finished prompt for too long.

## Running it

```bash
cd "c:/Models Python/shipgate_private"
vhs docs/demo.tape
```

This produces `docs/demo.gif`. Preview it before using it anywhere — VHS records the real
terminal output, so anything `demo_scenario.sh` prints (including its own timestamped demo
directory name) will be in the frame verbatim.

## Ship Report screenshots

No separate tool needed beyond a normal screenshot: run `demo_scenario.sh` (or the PowerShell
form) up through Step 8 (`shipgate report --project-dir .`) in a terminal window sized to look
good in a README, and capture that window directly. VHS can also produce this as a still frame
(set `Output docs/ship_report.png` in a second, shorter tape that stops right after Step 8) if a
scripted, reproducible capture is preferred over a manual one.

## What this session did and didn't do

- Researched VHS live (2026-09-07) rather than from memory; the PowerShell caveat above is from
  a real, cited GitHub issue, not assumed.
- Wrote the tape script above against VHS's documented syntax (`Output`, `Set Shell`, `Set
  FontSize`/`Width`/`Height`/`Theme`, `Type`, `Enter`, `Sleep`) — not executed, so an
  undocumented syntax drift in a newer VHS release would only surface when this is actually run.
- Did not install VHS, `ffmpeg`, or `ttyd` in this environment, and did not produce
  `docs/demo.gif` or any screenshot. That step needs a machine where a real terminal window can
  be rendered and captured — this analyst's environment doesn't have that.
