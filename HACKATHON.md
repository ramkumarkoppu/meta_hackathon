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
  `read` and `ask` first ran with the real Glimmer and Spark on simulated screens.
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
- **MIT licence (29 Oct).** The repo is now under the MIT licence ([LICENSE](LICENSE)), and it covers
  the pre-event groundwork too.
- **Ready to read the real screen (29 Oct).** Before Glimmer's first read of the real locked computer,
  `nanokvm read --check` now reports line numbers only, never the text read, so I can share its result.
  `read`, `ask` and `edit` check that Glimmer is up before asking to share the screen, refuse a frame
  more than 2 s old, and print the service's warnings about the picture. `run-glimmer.ps1` refuses to
  start a second server, and `nanokvm status` shows any active screen shares.
- **Spark finds the error itself (29 Oct).** `nanokvm fix`: with no request from me, Glimmer notes
  what is wrong on the screen (error messages, squiggles, the status bar's counts), and Spark gets those
  notes and the code, redacted, finds the error and fixes it through `apply_edit`. After each edit,
  Spark sees the screen as it now is; when it says it's done, Glimmer reads the screen afresh and Spark
  checks the fix there, editing again if needed: understand, think, act, understand again. With
  `--auto`, one yes at the start lets it type every edit without asking; each edit is still shown and
  checked on the screen before and after typing. On 29 Oct it found and typed the fix for a deliberate
  syntax error on the real locked computer.
- **The loop closes: first milestone (30 Oct).** Once Spark answers *FIXED* on a fresh reading of the
  screen, `fix` saves the file (Ctrl+S), with Glimmer checking first that VS Code has the keys and the
  right tab is selected, and after that the tab's unsaved-changes dot has gone. An earlier live run
  that day showed two gaps, then closed: the file's last line couldn't be edited (VS Code's status bar
  now shows when a selection ends at the file's end), and a line Glimmer read differently the second
  time ended the run (Spark now gets the screen as read then, and tries again). Then, on the real
  locked computer and with no description from me, `fix --auto` found junk typed on hello.cpp's last
  line, planned again when the second reading differed, deleted it, checked it on the screen, got
  *FIXED* from Spark on a fresh reading, and saved the file, checked on the screen: understand, think,
  act, understand, end to end (4 requests to Spark, $0.0085).
- **Asking from the live view (30 Oct, `assist.py`).** `nanokvm viewer` has a prompt box beside the
  live picture: type any question about what the locked computer shows, and Spark answers from
  Glimmer's redacted reading of the window in front (Glimmer is told to quote its messages exactly and
  to leave out other windows and notifications), or Glimmer answers alone with nothing sent. *Fix it*
  then runs the fix loop with the question as a hint. Every approval the terminal asks for is asked in
  the panel, beside the live picture; Stop releases every key. On the real locked computer on 30 Oct,
  a question found a deliberate `standard::cout` error ($0.0021); Fix it's first run stopped safely at
  the Go to Line check, which led to the next item. An independent review (3 reviewers, each checked by
  a skeptic) then tightened it: the test-content question comes with every share, approvals scroll and
  arm only once seen, and a hidden prompt box can't send.
- **Keys lost right after a start (30 Oct).** Every time the first keys went out right after the
  service started (or after an idle spell), the first Ctrl+G did nothing. That happened on 29 Oct's
  first typing run and twice on 30 Oct, and I had put it down to VS Code's focus. Now, before the first
  key or click after the link opens or a quiet minute, the service taps Scroll Lock until the locked
  computer's Scroll Lock light (reported back to the NanoKVM's chip) follows, then taps it back.
- **Glimmer reading the real screen (29 Oct).** Through the NanoKVM-USB, Glimmer on my GPU
  transcribed a 9-line C++ file in VS Code on the locked computer exactly, in 6.1 s, and
  `read --describe` named the app, the file and the status bar's contents correctly, in 6.7 s,
  with the DFlash drafter on (7.1 s and 13.5 s without it). For that, the NVIDIA GPU drives no
  display: Armoury Crate's GPU Mode is Standard, and my external monitor goes through a USB-C dock.
  `ask` on a real capture comes next. README section 6a has the whole loop, step by step.
