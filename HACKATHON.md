# What I built during the hackathon

This repo is my solo entry in the Indie Developer track of the Meta Global AI Developer Hackathon.
The build window opened on Mon 26 Oct 2026 at 12:00 PM ET (16:00 UK) and closes on Wed 4 Nov 2026
at 9:00 PM ET. Only work from the event counts, so this page shows where the line is.

## Before the event: groundwork, not part of the entry

Commit `e624738`, tagged `pre-event-baseline`, holds everything that existed before the build
window opened. I wrote its code and README between 7 and 25 Oct 2026. I took the snapshot at 16:16
UK on 26 Oct; on that day only `.gitignore` and planning notes in `docs/hackathon-plan.md` had
changed.

It has:

- the NanoKVM-USB bridge: HDMI capture, keyboard and mouse through the CH9329 chip, the local
  background service, the MCP server for Claude Code and the `nanokvm` command;
- automatic deletion of saved screenshots;
- the local Muse Glimmer setup (the llama-server launcher, smoke tests and A/B tools) and a smoke
  test for the Meta Model API.

## During the event: the entry

Every commit after `pre-event-baseline` was made during the build window, and the commit dates
show it. To see exactly what changed since the baseline:

```
git log --stat pre-event-baseline..HEAD
git diff pre-event-baseline
```

So far:

- **Meta models reading the screen (26 Oct).** Muse Glimmer runs on my own GPU and reads the
  locked computer's screen (`glimmer.py`, `nanokvm read`). Redaction (`redact.py`) and an outbound
  gate (`outbound.py`) mean Muse Spark only ever gets redacted text (`spark.py`, `pipeline.py`,
  `nanokvm ask`). The service gained full-resolution frames for the local model (`/api/frame`).
- **Any OS on the locked computer (26 Oct).** The `locked_os` setting covers Windows, macOS and
  Linux.
- **Live viewer (29 Oct).** `nanokvm viewer` shows the locked computer's screen live in a window.
- **Approved edits typed back (29 Oct).** `nanokvm edit`: Muse Spark proposes code edits through
  function calling (`apply_edit`). Each one is shown as a diff with the exact keys, typed into VS Code
  on the locked computer only after I say yes, keyboard only through the NanoKVM, and checked by Muse
  Glimmer on the screen before and after (`editing.py`).
- **The README rewritten around the Meta-model pipeline (29 Oct).** The Claude Code bridge, built before
  the event, moved to its own page, [docs/claude-code.md](docs/claude-code.md).
