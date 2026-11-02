# What I learned building Locked Computer Assist

Here is why Locked Computer Assist is built the way it is, and what I found Meta's models could and
couldn't do, up to 2 Nov 2026. Every hardware run used a spare Windows laptop standing in for the
locked computer, with only dummy files on it. I call it the stand-in laptop. [HACKATHON.md](HACKATHON.md)
lists what was built when.

## Why a USB KVM

Some computers may not run AI software: locked-down laptops, lab PCs, machines on separate networks. To
such a computer, the USB KVM I chose before the event is just a monitor and a USB keyboard and mouse.
Nothing is installed on it. Keystrokes can be sent but never read, unplugging the KVM's USB cable to the
locked computer stops all typing, and if a policy blocks new USB keyboards, only reading works. That
keeps within the ban in Meta's acceptable-use policy on "bypassing, disabling, or defeating any security
control" (read before the event, on 14 Oct, and again on 29 Oct), and the design assumes the computer's
owner approves.

The price is a narrow channel: a compressed 1920x1080 video at about 30 fps in, key presses and mouse
moves out. So the AI computer has to read small code from a picture and check on the screen that every
key did what it should. The service that drives the KVM's capture and keyboard was built before the
event.

The same policy requires human oversight and confirmation for consequential or irreversible actions, so
every edit is shown as a diff with the exact keys and typed only after a yes. `--auto` turns that into
one yes per run, which names typing and, for `fix`, saving. Each edit is still shown and checked, and the
first failed check stops the run. The live viewer's Fix it carries out a task one step at a time. It
shows each step before doing it, with its target outlined on the live picture, and a step that types or
changes something asks first, even under Auto (0bd6b07).

## Choosing the models

### Muse Glimmer on my own GPU

I checked Meta's line-up before the event (14–15 Oct). Muse Glimmer 30B, with open weights (Apache-2.0)
and about 19–20 GB at 4-bit with vision, was the only Meta model that could read a screen and fit the AI
computer's 24 GB RTX 5090 Laptop GPU. Muse Spark, Muse Wonder and Muse Code are cloud-only. Sending
screen images to any cloud would only move the privacy gap, so in this pipeline Glimmer is the only
model that sees pixels.

On 24 Oct, before the event, Unsloth's UD-Q4_K_XL, the same plus Meta's DFlash drafter, and Meta's own
Q4_K_M all transcribed a rendered 1920x1080 C++ screen with 27 of 27 lines right (compared with runs of
spaces collapsed, so indentation wasn't checked). The weights tied and only the drafter made reads
faster, so Unsloth's weights, chosen following Unsloth's guide, stayed the default. The drafter is
lossless, since llama.cpp keeps a drafted token only when it equals Glimmer's own pick. llama-server
drafts only 3 tokens a step by default, but the drafter's block size is 16, so the launcher drafts up to
15. That day, in a run whose GPU stayed near 55 W, generation went from 14 tokens/s without the drafter
to 23 at a draft length of 3 and 46 at 15.

### Muse Spark in Meta's cloud

Spark is there for its stronger reasoning, and it gets only redacted text. It answers questions and
proposes edits as text-only function calls with one tool, `apply_edit`, which replaces whole numbered
lines (7101624). Following Meta's documentation, checked before the event (24 Oct), the client sends
only to Standard model IDs, whose content Meta's terms exclude from training. It uses Chat Completions
with `store: false`, since the Responses API stores by default. The terms say a request "may" be routed
to a Contributor model, so a reply is discarded and sending halts if any streamed chunk names another
model. On 26 Oct Meta's Billing page said content sent on free credits "may be used for product
improvement", so on free credits nothing is sent until you confirm the screen shows only test or dummy
content.

Why not Glimmer for everything? It can do it all: with Local only it answers and writes the edits, and
Fix it plans with it by default. But on this laptop it's a 30B model at 4 bits, in one 32K-token slot
that it shares with the screen images, with its thinking capped so each step stays usable (a planning
step takes about 13.5 s). Spark is Meta's larger model, run in Meta's cloud, and by Meta's own figures
it is the stronger one at this kind of work: 80.8 on OSWorld-Verified for Muse Spark 1.1, against 65.9
for Glimmer 30B. I use muse-spark-1.3, the newest Spark the Model API offers me, but Meta reports only
OSWorld 2.0 scores for it, which can't be compared with Glimmer's, so 1.1's is the like-for-like figure.
So when a screen's redacted text may leave, Spark does the hard reasoning, such as
finding an error nobody described and checking the fix on a fresh reading, while the laptop's GPU only
reads the screen. I didn't run the two head to head on one task
([How Meta's models compared](#how-metas-models-compared)). The choice is made per screen: Local only
for anything redaction can't protect, Spark when redacted text may go.

### What I didn't use, and why

Checked before the event (14–15 Oct), and not used:

- **Meta's computer tool:** it works only through the Responses API, on pixels of a screenshot you
  send, so it would mean sending screen images to Meta. The computer-use agent my pre-event plan (kept
  in the `pre-event-baseline` tag) designed around it was never built. Glimmer finds targets locally
  instead ([Surprises](#surprises)).
- **Muse Spark Open** (816B parameters, 42B active): the smallest GGUF is 176 GB.
- **Muse Code and `-contributor` IDs:** in Contributor mode Meta trains on the content.
- **Llama 4:** doesn't fit in 24 GB, and is weak at tool calling.
- **SAM 3.1:** unproven on GUI screenshots.
- **Muse Image:** no role in reading a screen.

## Why local and cloud together

Glimmer reads the screen on the AI computer, so only text leaves it, and Spark brings its stronger
reasoning to that text. On its way to Spark the text is redacted: secrets with a recognisable shape,
email addresses, card numbers and terms you list become placeholders such as `[[EMAIL_1]]`. Glimmer
then trims it to the lines a question needs, and it is redacted again, checked and logged before it's
sent. Among other things, the checks look for any redacted value left and any image, and apply the
free-credit rule and a $25 budget. Placeholders are restored only on the AI computer.

Two limits shaped the rest:

- Redaction catches values with a recognisable shape and the terms you list, not names, patient
  details or confidential prose, so for screens like that the advice is to send nothing. With Local
  only, Glimmer answers on the AI computer and, since 1 Nov, writes the edits too (4108952). The
  assistant helps with software and its errors. It doesn't interpret patient data or give clinical
  advice.
- Meta's Glimmer paper (read before the event, on 19 Oct) shows screen reading flat across reasoning
  strengths, while privacy leakage on PrivacyLens is lowest at high reasoning, at 49.2%. So reads run at
  low reasoning and trimming at high. Even then Glimmer leaked in about half the benchmark's cases, so
  the trimmer isn't trusted on its own: its picks are matched back to the redacted lines, and the
  result is redacted again. `fix` has no question, so it skips trimming. That's why its notes prompt
  tells Glimmer to cover only the VS Code window and the file's bare name, which is an instruction to
  the model, not a crop. The notes are shown before they're sent (b21731e).

## What the models could and couldn't do

### Muse Glimmer

- **It read a short file exactly, and the status bar was the dependable reading.** On 29 Oct Glimmer
  transcribed a 9-line C++ file on the stand-in laptop exactly (checked by eye) in 6.1 s. No live fix
  run up to the first milestone stopped on a status-bar reading, while re-read code lines disagreed in
  three, so the checks around typing lean on VS Code's status bar and compare re-read code by its text
  only.
- **Three documented ways to cap its thinking were silently ignored** on llama.cpp b10976 (found before
  the event, on 24 Oct), because the build's chat handler for Glimmer sets no thinking tags. A
  per-request cap built from Glimmer's own tags worked. At 0, a transcription took 223 tokens on each of
  3 runs, against 279–610 uncapped. So readings don't think, and planning, trimming and coding get fixed
  budgets. Uncapped, Glimmer sometimes re-transcribed the whole screen in its reasoning, so only the
  reasoning's length is kept.
- **Thinking counts towards `max_tokens`.** The first time the task button (then called Do it) ran
  live, on the stand-in laptop on 30 Oct, every plan was cut off. Planning allowed 1,024 thinking
  tokens, but `max_tokens` was 400. Planning now gets 768 thinking tokens plus 512 for the answer
  (9e0d95d).
- **Two readings of the same code can differ.** The seventh live fix run on the stand-in laptop (around
  midnight on 29–30 Oct) stopped although nothing had changed. Most likely a re-read differed by a space
  of indentation, at the launcher's temperature of 1.0, set before the event. Greedy reads (b68880c)
  didn't settle it: in the ninth run (30 Oct) an unindented line read as 4 spaces, then 0. So
  indentation is measured in VS Code instead, with End, then Home, and the status bar's Col. In `fix`, a
  differing reading makes the coder plan again (e866965, 3237dd5).
- **Screen text could pose as a new turn.** A review by AI agents of the 1 Nov changes found that
  llama-server would read Glimmer's chat control tokens (`<|eom|>` and the like) in screen text as real
  ones, and Meta reports prompt-injection attacks on Glimmer succeeding 28.4% of the time (noted before
  the event). Such tokens now become look-alikes when screen text is quoted to Glimmer as the coder or
  for a local answer (0bd6b07).

### Muse Spark

- **Text alone was enough.** On 29 Oct on the stand-in laptop, Spark found a missing ';' on line 7 by
  itself, from Glimmer's redacted notes and the code. After the eighth run had fixed its error end to
  end, `fix` gained a save that needs Spark's FIXED on a fresh reading and your yes (3237dd5). In the
  first milestone on 30 Oct, with no description, Spark found junk typed on hello.cpp's last line and
  planned again when Glimmer read that line differently. It had the line deleted and checked, answered
  FIXED on a fresh reading, and the file was saved (4 requests, $0.0085; e93b307).
- **It read a refusal as a fact.** In the fourth fix run on the stand-in laptop (29 Oct), Spark was told
  *line 8 isn't on the screen, or can't be read reliably there* when the closing '}' was only held back
  as the last line read. It took the brace to be missing and added a second one, which passed every
  check. The checks prove the keys did what was planned, not that the plan was right. Refusals now say
  what they don't mean, and the final check looks for anything doubled or missing (81031b0).
- **It worked around a blanket guard.** In the ninth run (30 Oct), unable to edit the file's last line,
  it tried a line-continuation trick on the line above, refused only because Glimmer read that line's
  indentation differently the second time. Now the last line can be edited once the status bar shows
  it's the file's last, and the coder is told to fix the cause, not to hide it with a trick (3237dd5).

### llama.cpp and the Model API

- **Defaults and power.** Before the event (24 Oct), llama-server's defaults (4 slots of 131K context)
  ran Glimmer at about 5 tokens/s, CPU speed, until one slot with a 32K context kept it on the GPU.
  After that, speed depended mostly on the AI computer's power state. A full-screen read took 6.7–7.0 s
  at full power on the 240 W charger, but 23–26 s in a run whose GPU stayed near 55 W, so the launcher
  warns about battery, USB-C (PD) power and a quiet power mode.
- **The Model API worked** on event day 1 (26 Oct), from the UK on free credits: muse-spark-1.3
  answered in 1.6 s, strict JSON and streaming worked, and the smoke test (written before the event)
  cost $0.0035. So streamed Chat Completions with `store: false` and one function tool per request
  became the whole cloud interface.

## Typing blind

Every code edit is typed with the keyboard only, by line number, and each key that could change the file
waits for a positive check on the screen. After Ctrl+G, no digit or Enter goes out until Glimmer sees VS
Code's Go to Line box. The status bar must then read *Ln S, Col 1*. Ctrl+L's selection must end exactly
where planned before Backspace deletes it and the new lines are typed and checked. If a check fails
before the Backspace, only navigation keys were sent and the file is unchanged.

There were ten live fix runs on the stand-in laptop before the first milestone (29–30 Oct). Seven
stopped at a screen check having sent only navigation keys, so none of them changed the file. Run 5
stopped at the free-credit rule before any key went out. Run 8 fixed its error end to end. Run 4 typed
the right fix and then the one wrong change, a second '}', which passed every check.

- **The Go to Line box didn't look the way VS Code's source said.** The check was first based on VS
  Code 1.144's source, where the box opens with ':' already typed. On the stand-in laptop it opened
  empty, with the hint *Type a line number to go to (from 1 to 9).*, so runs 2 and 3 were refused
  although the box had opened, until a phone photo of the screen showed why (ee9bd0a).
- **The first keys after a quiet spell seemed to be lost.** Three times on the stand-in laptop, the Go
  to Line box wasn't seen when the first keys went out right after the service started or sat idle. Each
  time I put it down to VS Code's focus. After two of them, `nanokvm keytest`, which presses Num Lock
  twice and checks the state the locked computer reports back, said the keys were arriving. The first
  may instead have been the early check, which wanted a ':', refusing the empty box. Now, before the
  first key or click after the link opens or a quiet minute, the service taps Scroll Lock until the
  locked computer's light follows, as reported back through the KVM, then taps it back (29f8a60).
  Whether that cured it isn't recorded.
- **A click into code didn't land where it aimed.** On 1 Nov on the stand-in laptop, asked what was
  wrong with hello.cpp and to fix it, the task button (then called Do it) clicked into the code and
  typed `using namespace std;`, which landed below `main` instead of above it. The same plan was right
  on a synthetic copy of the screen, and nothing checked where the cursor landed. The likeliest cause is
  the setup. Monitor A was the stand-in laptop's extended display, not its main one, and the KVM's mouse
  targets the main display, so the click never reached the line. VS Code's rows are only about 19 pixels
  apart, too, even on the main display. Now the planner is told to hand a change to code to the fix
  loop, which edits by line number (4108952). Since that rests on the planner's choice, every typing
  step asks first, even under Auto, and says where the cursor is when the status bar shows it (0bd6b07).
  Later that day, on the stand-in laptop, Fix it made the right fix to hello.cpp and saved it.

## Surprises

- **Glimmer points well on its own 0-1000 scale**, which Meta's docs (read before the event) say its
  models use outside the computer tool. On a synthetic 1920x1080 screen with dummy names (30 Oct), it
  found 8 of 8 targets as points and as boxes, and answered NONE for a button that wasn't there
  (6b82d8c). So Fix it has Glimmer find every target on the AI computer and clicks the middle of its
  box. On the stand-in laptop, where Monitor A wasn't the main display, a click into code most likely
  missed ([Typing blind](#typing-blind)).
- **Glimmer can be a coder.** On 1 Nov llama-server returned Glimmer's tool calls as structured calls,
  so it gets the same request, system message and `apply_edit` tool as Spark. As planner and coder on a
  simulated VS Code drawn like the stand-in laptop's, it made the right fix to hello.cpp from two
  starting states, answered FIXED on a fresh reading and saved, with nothing sent (98 s and 96 s;
  0bd6b07).

## How I worked

- **Simulator first.** Each feature was tested first against the simulated locked computer (built
  before the event), with a mock Glimmer and a mock Meta API, and tests check that no image reaches the
  mock API. The suite grew from 111 tests before the event to 298 on 2 Nov, all passing. The simulator
  was optimistic where it mattered. Its mock Glimmer reads perfectly, the same way every time, and
  doesn't think, and its Go to Line box opens with ':' as in VS Code 1.144's source. So the empty box,
  the disagreeing re-reads and the thinking that used up the answer budget were first seen live, and
  each came back as a test.
- **Reviews by AI agents.** After most big steps, a review by AI agents (several reviewers, each checked
  by a skeptic agent) went over the change, and the commits record 115 findings from four of them. They
  caught what the tests hadn't. The Go to Line rule, loosened after the empty box, also accepted the Go
  To dialogs of Notepad, Visual Studio and Word, and was narrowed about half an hour later (b21731e). And
  a planner that said a typing step changed nothing could still type into code under Auto without asking
  (0bd6b07).
- **One rung at a time.** On 29 Oct I moved onto the stand-in laptop in steps: the viewer with the
  keyboard link never opened, then Glimmer's first read-only look, then fix runs, and saving only on 30
  Oct. Because screens can show confidential data, the typing checks' failure messages say what differed
  in words and numbers only, never screen text. That was enough to show exactly what went wrong in run
  9, an indentation read as 4 spaces, then 0.

## What I'd do next

None of this is built yet:

- a compile or test run alongside FIXED, which is only the coder's judgement on a fresh reading (run 4's
  doubled brace passed every screen check);
- approvals enforced by the service that drives the keyboard, as well as by the command line and the
  viewer;
- a smaller image budget for descriptions, the one big speed lever left (about 5.2 s of a 6.7 s
  description is the image), after a letter-by-letter legibility check;
- fine-tuning Glimmer on synthetic screens;
- typed edits and whole tasks beyond VS Code (no whole task outside it is recorded finishing yet);
- an AI computer on macOS or Linux (for now it needs Windows and an NVIDIA GPU of about 24 GB).

## Questions the submission asks, answered from this work

### Why are Meta's models important to the solution?

Muse Glimmer is what keeps screen images on the AI computer. When I checked before the event (14–15
Oct), it was the only Meta model that could read a screen and fit a 24 GB laptop GPU
([Choosing the models](#choosing-the-models)). Since 1 Nov it can run Fix it's whole loop with nothing
sent (tested end to end so far on a simulated VS Code), so a screen that redaction can't protect can
still get a fix. Muse Spark adds stronger reasoning where redacted text may leave.

### How I used Meta's products

Muse Glimmer, on the AI computer's GPU through llama.cpp with Meta's DFlash drafter (set up before the
event), does every model job that needs the picture: reading code with its line numbers, describing
windows, noting what's wrong for `fix`, checking each typed edit and the save on the screen, and finding
every Fix it target. By default it also plans Fix it's steps and writes its edits, and it trims text
before anything leaves. Muse Spark (muse-spark-1.3 on the Meta Model API) gets only redacted text. It
answers questions, finds errors and proposes fixes through `apply_edit`, and confirms FIXED on a fresh
reading. If ticked, it also plans Fix it's steps with a `ui_step` tool, naming targets in words for
Glimmer to find.

### The biggest obstacle

Acting, not reading. Glimmer's first read of the stand-in laptop was exact, but closing the loop took 11
live fix runs and 14 more commits, from the `fix` command's commit (22:39 UK on 29 Oct) to the
milestone write-up (09:59 on 30 Oct). Every problem was in typing, its checks or its consent step: the
Go to Line box not seen, its wording, two readings of a line disagreeing, the last line held back and
the free-credit confirmation. [Typing blind](#typing-blind) has the details.

### How Meta's models compared

I didn't measure any comparison with another model family. The entry uses only Meta's models, and
Glimmer and Spark weren't run head to head on one task. By Meta's own scores, checked before the event,
Glimmer 30B is weaker at computer use than Muse Spark 1.1 (OSWorld-Verified 65.9 against 80.8; for
muse-spark-1.3 Meta publishes only OSWorld 2.0 scores, which aren't comparable), yet it plans Fix it by
default because it sends nothing. Within the family, Unsloth's and Meta's 4-bit weights tied on the
pre-event test, and Meta's drafter made Glimmer faster.

### What mattered most when choosing a model

Roughly in this order, set before the event: keeping screen images on the AI computer (so open weights
with vision that fit 24 GB), the terms for what is sent, capability (an exact read of a 1080p capture,
pointing, tool calling), then cost and speed. Privacy and fit ruled options in or out. Cost didn't
constrain anything (a viewer question cost $0.0021, the milestone fix $0.0085), and speed only tuned the
setup: a fix run with Glimmer takes about 1.5 minutes.

### What would make switching hard

The local side depends on llama.cpp b10976, pinned before the event. The thinking cap built from
Glimmer's own tags relies on how that build behaves, so it has to be re-tested after any upgrade. That
build doesn't enforce a JSON schema, so trimming asks for plain text. And a later llama.cpp change halves
image-prompt speed with the drafter (llama.cpp issue #29982). Reasoning strength also goes in each
request, because Unsloth's GGUF carries Meta's old chat template. Pointing and image sizing are built
around Glimmer too, from its 0-1000 boxes to frames sized for its vision encoder, so another local model
would need all of that redone and checked again. The coder is the easiest part to switch: the fix loop
makes the same call to Spark or Glimmer. Another cloud model would mean rewriting the guards in
`spark.py` and re-checking its provider's terms.

### What would stop production use

Meta's data rules as I found them. On 26 Oct the Billing page said content sent on free credits "may be
used for product improvement", so anything but test content needs pay-as-you-go billing. Before the
event I'd read that a request "may" be routed to a Contributor model (which is why the client checks the
model named on the stream), that Meta may keep content for safety and abuse review even with
`store: false`, and that zero data retention is by request only. The design assumes the computer's owner
approves redacted text going to Meta, and screens that redaction can't protect should use Local only.
Then there are the limits in [What I'd do next](#what-id-do-next), above all that nothing compiles or
runs the code. And every hardware run so far used the stand-in laptop with dummy files.

### Partner technologies

I didn't use Fireworks, Modal or Baseten, so I have no first-hand feedback on them. My pre-event plan
noted that their credits were for select participants and only for running Meta's open-weights models
on their clouds. The design didn't need them: a partner host would be one more cloud to send screens or
text to, and Glimmer already runs on the AI computer's own GPU.
