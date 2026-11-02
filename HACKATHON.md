# What I built during the hackathon

This repo, Locked Computer Assist, is my solo entry in the Indie Developer track of the Meta Global AI
Developer Hackathon (theme *Build for Your World*). The build window opened on Mon 26 Oct 2026 at noon
US time (16:00 or 17:00 UK, see below) and closes on Wed 4 Nov 2026 at 9:00 PM ET (Thu 5 Nov, 02:00 UK).
Only work from the event counts, so this page shows where the line is.

## Before the event: groundwork, not part of the entry

Commit `e624738`, tagged `pre-event-baseline`, holds everything that existed before the build
window opened. I wrote its code and README between 7 and 25 Oct 2026, and took the snapshot at 16:16
UK on 26 Oct. That day, before the snapshot, I only added notes from the kickoff to
`docs/hackathon-plan.md` and wrote `.gitignore`; no code changed. The plan is still in the baseline,
but I took it out of the current files on 1 Nov because it was out of date.

The start time depends on where you look. The kickoff slides said 12:00 PM ET on 26 Oct, which was
16:00 UK because US clocks hadn't changed yet; Cerebral Valley's event record, like my acceptance email,
says 12:00 PM GMT-5, which is 17:00 UK. So the snapshot came 16 minutes after the first and 44 minutes
before the second, and a check of the repo against the event's start time may flag it as an early
commit. That's expected: this commit is the pre-event baseline, and nothing in it is part of the
entry. On 29 Oct, before the first push, I reworded the commit messages, including the baseline's
(which gave the start as 17:00 UK); every commit's files and dates are unchanged.

It has:

- the NanoKVM-USB software: HDMI capture, keyboard and mouse through the CH9329 chip, the local
  background service and the `nanokvm` command;
- an MCP server that let an AI coding assistant use the service, which I took out of the current
  files on 2 Nov because the entry never used it;
- automatic deletion of saved screenshots;
- a simulated locked computer (a Notepad window) for the tests; its small VS Code and its Num and
  Scroll Lock lights came during the event;
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

The first event commit also renamed the two computers (now the locked computer and the AI computer) and
the project folder (now `E:\meta_hackathon`), so the diff shows those renames in pre-event files too.

Every run on the real hardware below used a spare PC standing in for the locked computer, with only
dummy files on it, so *the locked computer* in those results, and in the commit and tag messages, means
that PC.

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
  Glimmer on the screen before and after (`editing.py`). It was first tested on the simulator, with a
  mock Glimmer and a mock Meta API; the same typing ran with the real models on the stand-in PC later
  that day, through `nanokvm fix` (below).
- **The README rewritten around the Meta-model pipeline (29 Oct).** It now opens with what the entry
  does, its results and limits, and what was built before the event.
- **MIT licence (29 Oct).** The repo is now under the MIT licence ([LICENSE](LICENSE)), and it covers
  the pre-event groundwork too.
- **Ready to read the real screen (29 Oct).** Before Glimmer's first read of the stand-in PC,
  `nanokvm read --check` now reports line numbers only, never the text read, so I can share its result.
  `read`, `ask` and `edit` check that Glimmer is up before asking to share the screen, refuse a frame
  more than 2 s old, and print the service's warnings about the picture. `run-glimmer.ps1` refuses to
  start a second server, and `nanokvm status` shows any active screen shares.
- **Glimmer reading the real screen (29 Oct).** Through the NanoKVM-USB, Glimmer on my GPU
  transcribed a 9-line C++ file in VS Code on the stand-in PC exactly, in 6.1 s, and
  `read --describe` named the app, the file and the status bar's contents correctly, in 6.7 s,
  with the DFlash drafter on (7.1 s and 13.5 s without it). For that, the NVIDIA GPU drives no
  display: the laptop runs in its hybrid graphics mode, and my external monitor goes through a USB-C
  dock.
  A question from the viewer, through the same outbound gate as `nanokvm ask`, was answered from the
  real screen on 30 Oct (below). README section 6a has the whole loop, step by step.
- **Spark finds the error itself (29 Oct).** `nanokvm fix`: with no request from me, Glimmer notes
  what is wrong on the screen (error messages, squiggles, the status bar's counts), and Spark gets those
  notes and the code, redacted, finds the error and fixes it through `apply_edit`. After each edit,
  Spark sees the screen as it now is; when it says it's done, Glimmer reads the screen afresh and Spark
  checks the fix there, editing again if needed: understand, think, act, understand again. With
  `--auto`, one yes at the start lets it type every edit without asking; each edit is still shown and
  checked on the screen before and after typing. On 29 Oct it found and typed the fix for a deliberate
  syntax error on the stand-in PC.
- **The loop closes: first milestone (30 Oct).** Once Spark answers *FIXED* on a fresh reading of the
  screen, `fix` saves the file (Ctrl+S), with Glimmer checking first that VS Code has the keys and the
  right tab is selected, and after that the tab's unsaved-changes dot has gone. An earlier live run
  that day showed two gaps, then closed: the file's last line couldn't be edited (VS Code's status bar
  now shows when a selection ends at the file's end), and a line Glimmer read differently the second
  time ended the run (Spark now gets the screen as read then, and tries again). Then, on the
  stand-in PC and with no description from me, `fix --auto` found junk typed on hello.cpp's last
  line, planned again when the second reading differed, deleted it, checked it on the screen, got
  *FIXED* from Spark on a fresh reading, and saved the file, checked on the screen: understand, think,
  act, understand, end to end (4 requests to Spark, $0.0085).
- **Asking from the live view (30 Oct, `assist.py`).** `nanokvm viewer` has a prompt box beside the
  live picture: type any question about what the locked computer shows, and Spark answers from
  Glimmer's redacted reading of the window in front (Glimmer is told to quote its messages exactly and
  to leave out other windows and notifications), or Glimmer answers alone with nothing sent. *Fix code*
  ran the fix loop with the question as a hint. It was first called *Fix it*; on 1 Nov it was folded
  into Do it, and Do it took the name *Fix it*. Every approval the terminal asks for is asked in the
  panel, beside the live picture; Stop releases every key. On the stand-in PC on 30 Oct, a question
  found a deliberate `standard::cout` error ($0.0021); the fix loop's first run there stopped safely at
  the Go to Line check, which led to the keyboard wake-up below. A review by AI agents (3 reviewers,
  each checked by a skeptic) then tightened it: the test-content question comes with every share,
  approvals scroll and arm only once seen, and a hidden prompt box can't send.
- **Do it (now *Fix it*): any program, not just code (30 Oct, `acting.py`).** I wanted the assistant
  for anything on the locked computer, such as a requirements tool, not only code in VS Code. *Do it*
  carries out a described task one step at a time (click, double-click, right-click, type, keys,
  scroll). Muse Glimmer, on my GPU, looks at the screen, plans the next step and finds its target, so
  by default nothing leaves the laptop; Spark can plan instead, from the redacted description, but
  Glimmer still finds every target locally. A test on a synthetic screen first showed Glimmer points
  accurately on its own 0-1000 scale (8 of 8 targets, and *NONE* for one that wasn't there;
  `tools/glimmer_point.py` repeats it). Each step is outlined on the live picture and allowed by a
  click, or by one yes with Auto, except steps that change something. Its first live run stopped at
  once: Glimmer's thinking used up the whole answer budget, now fixed. A review by AI agents (3
  reviewers, each checked by a skeptic; 43 findings, none refuted) then hardened it: keys are judged
  by what they are, not how they are spelt (every name of the Windows key is refused; Enter, Delete,
  Ctrl+V and the rest always ask), anything but a plain "no" counts as a change, a box must be just
  four numbers, the outline is drawn where the click goes, and under Auto each step is shown before
  it is done.
- **Indentation from VS Code, not the picture (30 Oct).** After a fix, the code wasn't always tidy:
  Glimmer can read a line's indentation a few spaces off, and Spark copies it. Now the indentation of
  every line an edit replaces, and of every line it types, is measured in VS Code itself (End, then
  Home, puts the cursor on the line's first character; the status bar's Col says where). A line read
  wrong is put right in what Spark sees, and Spark plans again; a typed line indented other than as
  typed fails the check. `fix` asks Spark to leave what it changes indented like the code around it,
  and its final check looks for misindented lines too.
- **Keys lost right after a start (30 Oct).** Every time the first keys went out right after the
  service started (or after an idle spell), the first Ctrl+G did nothing. That happened on 29 Oct's
  first typing run and twice on 30 Oct, and I had put it down to VS Code's focus. Now, before the first
  key or click after the link opens or a quiet minute, the service taps Scroll Lock until the locked
  computer's Scroll Lock light (reported back to the NanoKVM's chip) follows, then taps it back.
  `nanokvm keytest`, which I built during the event on 29 Oct, checks that the locked computer takes
  the NanoKVM's keys: it presses Num Lock twice and takes no picture.
- **Code by line number, not by a click, and Glimmer as the coder (1 Nov, `localcoder.py`).** Asked
  *Whats wrong with this and fix it* about hello.cpp, Do it typed `using namespace std;` below `main`
  instead of above it: it had clicked into the code and typed, and nothing checked where the cursor
  landed. On a synthetic copy of the screen Glimmer's plan was right, so the click was at fault, most
  likely because Monitor A was the stand-in PC's extended display, not its main one, and the NanoKVM's
  mouse targets the main display (VS Code's rows are only about 19 pixels apart anyway). Now its
  planner is told never to click into code: for a change to code shown with line numbers, it hands the
  task to the fix loop
  (edits by line number, each approved and checked on the screen, *FIXED* on a fresh reading, then the
  save), with the task itself as the request. The coder is the planner's model, and Glimmer can now be
  it, on my GPU: it gets the same instructions and `apply_edit` tool as Spark, through llama-server's
  tool calls, so nothing is sent (`nanokvm edit --local-only` and `fix --local-only` use it too). That
  made the *Fix code* button redundant, so it's gone, and the one button that acts, Do it, is now called
  *Fix it*; its Auto asks once before a fix's edits and save. A review by AI agents (3 reviewers, each
  checked by a skeptic; 15 findings, none refuted) then tightened it: the hand-over rests on the
  planner's choice, so a step that types now always asks, Auto or not, and says where the editor's
  cursor is; the task, not a bug hunt, is the coder's request; Glimmer's chat control tokens in screen
  text (`<|eom|>` and the like) can't reach it as real ones; and the reports name what was sent. With
  the real Glimmer as planner and coder, on a simulated VS Code drawn like the stand-in PC's: from
  the original file it put `using namespace std;` above `main` (98 s); from the state Do it had left, it
  moved that line above `main` in one edit (96 s); each time it answered *FIXED* on a fresh reading and
  the file was saved and checked, with nothing sent. On the stand-in PC, asked the same question about
  hello.cpp, Fix it made the right fix and saved the file.
- **Tidying up for submission (1–2 Nov).** A last review by AI agents (4 reviewers, each checked by a
  skeptic) went over the code, the docs and everything that goes public. I applied its corrections
  and took the out-of-date pre-event plan out of the current files (the baseline keeps it). Then I
  wrote [LEARNINGS.md](LEARNINGS.md): why it's built this way, what I found Meta's models could and
  couldn't do, and answers to the submission form's questions. On 2 Nov I took out of the current
  files the pre-event MCP server, the page I had moved its instructions to, and the hook I added on
  29 Oct to keep that coding assistant out of `.run\`, with their tests, since the entry runs only on
  Muse Glimmer and Muse Spark (git history keeps them).
