# What I built during the hackathon

This repo is my solo entry in the Indie Developer track of the Meta Global AI Developer Hackathon.
The build window opened on Mon 26 Oct 2026 at 12:00 PM ET (16:00 UK) and closes on Wed 4 Nov 2026
at 9:00 PM ET. Only work from the event counts, so this page shows where the line is.

## Before the event: groundwork, not part of the entry

Commit `e624738`, tagged `pre-event-baseline`, holds everything that existed before the build
window opened. I wrote its code and README between 7 and 25 Oct 2026. I took the snapshot at 16:16
UK on 26 Oct, 16 minutes after the window opened. In those minutes I only added notes from the
kickoff to `docs/hackathon-plan.md` and wrote `.gitignore`; no code changed. On 29 Oct, before the
first push, I reworded the commit messages, including the baseline's, which had given the start as
17:00 UK instead of 16:00. Every commit's files and dates are unchanged.

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

or, on GitHub: <https://github.com/ramkumarkoppu/meta_hackathon/compare/pre-event-baseline...master>.

The first event commit also renamed the two computers (now the locked computer and the AI computer),
the project folder (now `E:\meta_hackathon`) and the Claude Code MCP server (now `locked-computer`).
So the diff shows those renames in pre-event files too. The Claude Code bridge itself changed only
for the renames, the `locked_os` setting, a pointer to the viewer window in `screen_status`'s reply
and, on 29 Oct, the rules that keep it out of `.run\`.

So far:

- **Meta models reading the screen (26 Oct).** Muse Glimmer runs on my own GPU and reads the
  locked computer's screen (`glimmer.py`, `nanokvm read`). Redaction (`redact.py`) and an outbound
  gate (`outbound.py`) mean Muse Spark only ever gets redacted text (`spark.py`, `pipeline.py`,
  `nanokvm ask`). The service gained full-resolution frames for the local model (`/api/frame`).
  So far `read` and `ask` have run with the real Glimmer and Spark on simulated screens, not yet on
  a real capture.
- **Any OS on the locked computer (26 Oct).** The `locked_os` setting covers Windows, macOS and
  Linux.
- **Live viewer window (29 Oct).** `nanokvm viewer` now shows the locked computer's screen live in a
  window on the AI computer. Before the event it only opened the browser live view, which is
  pre-event work.
- **Approved edits typed back (29 Oct).** `nanokvm edit`: Muse Spark proposes code edits through
  function calling (`apply_edit`). Each one is shown as a diff with the exact keys, typed into VS Code
  on the locked computer only after I say yes, keyboard only through the NanoKVM, and checked by Muse
  Glimmer on the screen before and after (`editing.py`). So far it has run only on the simulator,
  with a fake Glimmer and a fake Meta API: not yet with the real models or on a real computer.
- **Claude Code kept out of `.run\` (29 Oct).** Deny rules and a hook (`tools/guard_run_dir.py`) stop
  Claude Code in this folder from opening `.run\` by mistake, where the Meta API key, the service
  token and the audit log of what was sent to Spark live. They guard against mistakes, not a
  determined program, and the bridge's `screen_status` still returns the token
  ([docs/claude-code.md, section 3](docs/claude-code.md#3-approvals-and-what-goes-to-anthropic)).
- **The README rewritten around the Meta-model pipeline (29 Oct).** The Claude Code bridge, built before
  the event, moved to its own page, [docs/claude-code.md](docs/claude-code.md).
