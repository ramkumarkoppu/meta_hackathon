# Locked Computer Assist

**AI help for a locked computer: Muse Glimmer and Muse Spark through a USB KVM.**

Built during the Meta Global AI Developer Hackathon, on groundwork from before it: [what was built
when](#hackathon-entry), with the detail in [HACKATHON.md](HACKATHON.md).

Some computers may not run AI software: a locked-down work laptop, a lab PC, a machine on a separate
network. With the approval of whoever owns such a computer and its data, this project gives it AI help
without installing anything on it and without getting around any of its controls. A small USB KVM sits
between that computer, the **locked computer**, and a second one, the **AI computer**. To the locked
computer the KVM is only a monitor and a USB keyboard and mouse. If a policy there blocks new USB
keyboards, typing isn't available.

1. **Capture.** The KVM passes the locked computer's HDMI picture to the AI computer as a USB video
   device. Nothing is installed on the locked computer.
2. **Read locally.** Meta's open-weights **Muse Glimmer** runs on the AI computer's GPU (llama.cpp) and
   reads the screen: code with its line numbers, a description, or the error messages. In this
   pipeline, screen images never leave the AI computer.
3. **Redact.** Keys, tokens, passwords in `key = value` form, email addresses, common hostnames and the
   terms you list become placeholders. Glimmer trims the text to what the question needs, and it's
   redacted again.
4. **Ask the cloud.** Only that redacted text goes to **Muse Spark**, Meta's stronger cloud model
   ([why both](LEARNINGS.md#local-and-cloud-together)), through Meta's Model API (`store: false`).
   Spark answers, or proposes code edits as function calls. With *Local only*
   (`--local-only`), Glimmer does both on the AI computer instead, and nothing is sent.
5. **Act, with your approval.** You approve each edit at the keyboard, or once per run with `--auto`.
   The KVM's keyboard types it into VS Code on the locked computer, Glimmer reads the screen again to
   check it, and `fix` saves the file once the model that wrote the fix has checked it on a fresh
   reading and you've allowed it.

It's for people who work on such computers and get stuck on an error or a bug with no AI help allowed,
and for the organisations that own those computers and want to keep their data in. It's harder than it
sounds. The AI computer gets only a compressed video of the screen and can only press keys and move the
mouse, so it has to read small code from that picture, keep confidential text from leaving, and check
on the screen that every key it typed did what it should.

The aim is to help with whatever the locked computer shows: code, and the errors and messages of its
software tools, often locked-down legacy tools nothing else can help with. `read --describe` names the
application and quotes its messages and errors. In the viewer's prompt box you can ask about any
program, and **Fix it** takes a task in any program one step at a time, showing you each step first
([section 7](#fix-it)). Typed code edits work in VS Code, by line number, with every edit checked.

<a id="sensitive-screens"></a>**Sensitive screens.** The locked computer may show patient records or
company-confidential data. Its screen images never leave the AI computer, and redaction catches values
with a recognisable shape (keys, tokens, passwords in `key = value` form, email addresses, common
hostnames, card numbers, network paths and user names in profile paths) and the terms you list
([section 2](#2-what-leaves-the-ai-computer)). It can't reliably catch names, patient details or
confidential prose, so when such data is on the screen, send nothing. `nanokvm read` never sends.
`ask --local-only`, or `outbound = "local_only"` in `nanokvm.toml`, has Glimmer answer on the AI
computer instead of Spark, and `edit --local-only` and `fix --local-only` have Glimmer propose the edits
too. In the viewer, a question sends nothing with *Local only* ticked, and Fix it sends nothing unless
you tick Spark. The assistant helps with the software and its errors. It doesn't interpret patient data
or give clinical advice.

**Status (2 Nov).** `read`, `fix` and the viewer's Ask and Fix it have run end to end with the real
models: Muse Glimmer on the AI computer's GPU, and Muse Spark through Meta's API wherever text was sent.
Every hardware run used a spare Windows laptop standing in for the locked computer (the *stand-in
laptop* below), with only dummy files on it. My first milestone was the 30 Oct `fix --auto` run in the
table: with no description from me, it found junk typed on a file's last line, fixed it and saved the
file. [Section 6a](#6a-the-whole-loop-step-by-step) walks through that loop. All 298 automated tests
pass ([section 12](#12-files-and-testing-without-hardware)).

**Results so far** (on the stand-in laptop unless it says otherwise; dummy files only):

| Date | What | Result |
|------|------|--------|
| 29 Oct | The viewer window | The locked computer's screen live at about 30 fps, a median 37 ms from capture to window |
| 29 Oct | Glimmer reads a 9-line C++ file through the KVM, then describes the screen | Exact (checked by eye) in 6.1 s; the description right in 6.7 s (7.1 s and 13.5 s without the drafter) |
| 29 Oct | `fix --auto` on a deliberate syntax error | Found it, typed the fix and checked it on the screen, but also added a second '}' that passed every check (now caught) |
| 30 Oct | `fix --auto` with no description: junk on a file's last line | Found and deleted it, *FIXED* on a fresh reading, saved and checked; 4 requests to Spark, $0.0085 |
| 30 Oct | A question in the viewer about a deliberate `standard::cout` error | Found it; $0.0021 |
| 30 Oct | Glimmer finding buttons, folders and fields on a synthetic screen (`tools\glimmer_point.py`) | 8 of 8, and *NONE* for one that wasn't there |
| 1 Nov | Fix it on a simulated VS Code screen, with the real Glimmer planning and coding | The right fix, *FIXED*, saved, nothing sent (98 s and 96 s) |
| 1 Nov | Fix it: *Whats wrong with this and fix it*, on hello.cpp | Handed to the fix loop: the right fix, saved |
| 2 Nov | The automated tests (`pytest`; no hardware needed) | All 298 pass |

**Known limits.**

- Typed edits work only in VS Code, in small steps: at most 4 edits and 8 requests to the coder a run,
  at about 20 characters a second. `nanokvm edit` with a request of its own has only run against the
  simulator.
- Fix it hands a change to code to `fix`'s loop because its planner chooses to, which isn't guaranteed,
  so every step that types asks first, even with Auto. I haven't yet recorded it finishing a whole
  task outside VS Code. Its clicks need Monitor A to be the locked computer's main display, which I
  haven't tested. Typed edits work either way ([section 4](#4-locked-computer-settings)).
- *FIXED* is the coder's judgement on a fresh reading of the screen. Nothing compiles or runs the code.
- Redaction can't catch names, patient details or confidential prose: for those, use Local only and
  send nothing ([Sensitive screens](#sensitive-screens)).
- Text on the screen can still mislead a model (prompt injection), so every step and edit is shown
  before it's done.
- For now the AI computer must run Windows with an NVIDIA GPU of about 24 GB, and the locked computer
  has only been tried with Windows 11.

<a id="hackathon-entry"></a>**Hackathon entry.** This repo is my solo entry in the Meta Global AI
Developer Hackathon (Indie Developer track, theme *Build for Your World*).

- **Built before the event** (7–25 Oct, git tag `pre-event-baseline`): the nanokvm service (capture,
  keyboard and mouse), the `nanokvm` command's `diag` and `status`, the simulated locked computer the
  tests use, the local Glimmer server setup (llama.cpp, the weights, `tools\run-glimmer.ps1` and the
  smoke tests), and a smoke test for the Meta Model API. Pre-event features the entry didn't need were
  taken out on 2 Nov. A repo check may flag the baseline commit as early, and
  [HACKATHON.md](HACKATHON.md#before-the-event-groundwork-not-part-of-the-entry) explains why.
- **Built during the event:** Glimmer reading and checking the screen (`glimmer.py`), redaction and
  the outbound gate, the Spark client, `nanokvm read`, `ask`, `edit`, `fix` and `keytest`, the viewer
  window, its prompt box for asking about the screen and having a task done there (Fix it), Glimmer as
  a coder on the AI computer, and the simulated VS Code the tests type into.

[HACKATHON.md](HACKATHON.md) has the details, change by change, and [LEARNINGS.md](LEARNINGS.md) why
it's built this way and what I learned about Meta's models. Demos use the stand-in laptop showing dummy
code, never a real work computer. No KVM or GPU? [Section 12](#12-files-and-testing-without-hardware)
runs the tests and a simulated screen.

---

## Contents

1. [How it works](#1-how-it-works)
   - [1a. Operating systems](#1a-operating-systems)
2. [What leaves the AI computer](#2-what-leaves-the-ai-computer)
3. [Wiring for this desk](#3-wiring-for-this-desk)
   - [3a. Parts list](#3a-parts-list)
4. [Locked computer settings](#4-locked-computer-settings)
5. [Install](#5-install)
   - [5a. Muse Glimmer on the AI computer's GPU (open weights)](#5a-muse-glimmer-on-the-ai-computers-gpu-open-weights)
   - [5b. Muse Spark through Meta's Model API](#5b-muse-spark-through-metas-model-api)
6. [Using it: read, ask, edit and fix](#6-using-it-read-ask-edit-and-fix)
   - [6a. The whole loop, step by step](#6a-the-whole-loop-step-by-step)
7. [Watching the screen yourself, and asking about it](#7-watching-the-screen-yourself-and-asking-about-it)
8. [First run with the hardware](#8-first-run-with-the-hardware)
9. [Settings (nanokvm.toml)](#9-settings-nanokvmtoml)
10. [Safety details](#10-safety-details)
11. [Troubleshooting](#11-troubleshooting)
12. [Files and testing without hardware](#12-files-and-testing-without-hardware)
13. [Licence](#13-licence)

---

## 1. How it works

```
locked computer ──HDMI──► USB KVM ──USB──► AI computer
                                               │
 1. capture   nanokvm service                  │ the only program that opens the KVM
 2. read      Muse Glimmer on the GPU          │ screen → text; images never leave
 3. redact    outbound gate                    │ placeholders, trimming, checks, audit log
 4. ask       Muse Spark (Meta Model API)      │ redacted text in; an answer or apply_edit calls out
    or        Muse Glimmer as the coder        │ with --local-only, and in Fix it by default: nothing is sent
 5. act       you approve; the KVM types it    │ Glimmer checks the result on the screen (--auto: one yes a run)
locked computer ◄──USB keyboard── USB KVM ◄────┘
```

| Part | Runs on | Job |
|------|---------|-----|
| USB KVM | hardware | Gives the AI computer the locked computer's HDMI picture as a USB capture device (up to 1920x1080), and passes it through to Monitor A. With cable 4, it's also a USB keyboard and mouse for the locked computer. |
| `nanokvm` service | AI computer, in the background | The **only** program that opens the KVM. It reads the video continuously (about 30 fps), sends keystrokes, and serves the live picture to the viewer window, on `127.0.0.1` only. It starts automatically and stops after 60 idle minutes (an open viewer keeps it running). |
| Muse Glimmer | the AI computer's GPU, through llama.cpp's `llama-server` on `127.0.0.1:8001` | Reads the screen (code with line numbers, a description, or errors), notes what is wrong for `fix`, trims text before it leaves, and checks typed edits and the save on the screen. It also plans Fix it's steps (by default) and finds each target on its own 0-1000 scale. With Local only (or `--local-only`) it answers and writes the edits itself, through llama-server's tool calls, so nothing is sent, and by default it's Fix it's coder too. |
| Muse Spark | Meta's cloud, through the Model API | Answers questions about the screen and proposes edits with one tool, `apply_edit`. In `fix` it finds the error itself and checks the fix on a fresh reading (*FIXED*). With *Fix it: Spark plans and fixes code* ticked, it plans Fix it's steps with `ui_step` and is the coder. It only ever sees redacted text. |
| `nanokvm` command | AI computer | `read`, `ask`, `edit` and `fix` ([section 6](#6-using-it-read-ask-edit-and-fix)), the live `viewer` ([section 7](#7-watching-the-screen-yourself-and-asking-about-it)), and `diag`, `status` and `keytest` ([section 8](#8-first-run-with-the-hardware)). |

**Why two models.** Glimmer can do the whole job on its own. Spark is there for headroom where redacted
text may leave: by Meta's own scores it's the stronger model at computer use, though I haven't run the
two head to head. It was also the coder in every fix run on the stand-in laptop on 29–30 Oct, before
Glimmer could be one. The reasons, results and limits are in
[LEARNINGS.md](LEARNINGS.md#local-and-cloud-together).

### 1a. Operating systems

The two computers don't need the same operating system. To the locked computer, the KVM is a monitor
and a USB keyboard and mouse. To the AI computer, it's a USB video capture device (with an audio device
beside it for any HDMI sound, which the service never opens) and a USB serial port.

| Computer | Windows | macOS | Linux |
|----------|---------|-------|-------|
| Locked computer | Picture, Glimmer's reading, keyboard and mouse tested (Windows 11) | Should work; not tested | Should work; not tested |
| AI computer | Tested (Windows 11) | Not yet: needs porting | Not yet: needs porting |

**A locked computer on macOS or Linux.** Set `locked_os = "macos"` or `"linux"` in `nanokvm.toml`
([section 9](#9-settings-nanokvmtoml)). None of this has been tried yet.

- On a Mac, `edit` and `fix` select lines with Cmd+L and save with Cmd+S. Go to Line stays Ctrl+G.
  Section 4's other VS Code shortcuts use Cmd too (Cmd+Shift+P, Cmd+B, Cmd+J).
- `keyboard_layout` must match the layout chosen on that computer. `"uk"` and `"us"` are PC layouts,
  and Linux's English (UK) matches `"uk"`. A Mac's British layout puts some characters elsewhere, so on
  a Mac add the U.S. input source and set `"us"`. If *Keyboard Setup Assistant* asks for the keyboard
  type the first time the Mac sees the KVM's keyboard, choose ANSI, which matches `"us"`.
- Extend the displays rather than mirroring them, and for Fix it's clicks make Monitor A the main
  display. On macOS 13 or later, select it in *System Settings > Displays* and set *Use as* to *Main
  display*. On macOS 12 or earlier, drag the white menu bar onto it in *System Preferences > Displays >
  Arrangement*. On Linux the KVM's mouse may span every display that is on (it does on X11), so keep
  Monitor A the only display (GNOME: *Settings > Displays*).

**The AI computer must run Windows for now.** Capture uses DirectShow, so on macOS or Linux it stops
with *video capture needs Windows on the AI computer*, and the Glimmer launcher is a PowerShell script
for the Windows CUDA build of llama.cpp.

## 2. What leaves the AI computer

| What | Where it goes |
|------|---------------|
| The screen picture | **Nowhere**, with `read`, `ask`, `edit`, `fix` and the viewer's prompt box: Muse Glimmer reads it on the AI computer's GPU, and nothing in this project writes it to disk (below). |
| Redacted text | To Muse Spark through Meta's Model API, only through the outbound gate below. |
| Spark's answer and edits | Back to the AI computer, where placeholders are restored. Edits are typed only after you approve them: each one, or a whole run's once with `--auto` ([section 6](#6-using-it-read-ask-edit-and-fix)). |
| Fix it, in the viewer | **Nothing**, by default: Glimmer plans each step and finds its target on the AI computer, and for a change to code it also proposes the edits. With *Spark plans and fixes code* ticked, Spark gets Glimmer's redacted description of the window in front, through the outbound gate, and names each step's target in words, and a change to code goes to Spark as `fix` sends it. The picture and the screen positions stay on the AI computer either way. Each step and each edit is done only after you allow it ([section 7](#fix-it)). |
| A record of both | `.run\outbound.log` on the AI computer: exactly what was sent and received. It holds redacted screen text, so treat it as sensitive. |

**What stays in memory, and what can't be wiped.** Nothing in this project writes a capture of the
screen to disk. Each frame is dropped as soon as Glimmer has read it, and Glimmer's thinking text is
discarded. The service zeroes the pixel arrays it crops for Glimmer. The encoded copies are freed, not
wiped. The capture forgets its last frame when the video stops, and the service drops the viewer's
cached picture once nobody is watching. Glimmer's llama-server slot is flushed after every image, and a
failed flush is logged. Text-only requests to Glimmer aren't flushed. Windows can still page memory to
disk or hibernate.

**The outbound gate** (`src\nanokvm_mcp\outbound.py`) is the only path from screen text to Meta:

1. **Redaction** replaces private keys, API keys (AWS, GitHub, Slack, Google, Meta, `sk-…`), JWTs,
   passwords and secrets in `key = value` form, URLs, email addresses, network (UNC) paths, user names
   in `C:\Users\…` and `/home/…` paths, IP and MAC addresses, card numbers (Luhn-checked), long random
   tokens, and lower-case hostnames ending in `.com`, `.org`, `.gov`, `.edu`, `.mil`, `.local`,
   `.corp`, `.internal`, `.intranet`, `.lan`, `.co.uk`, `.ac.uk`, `.gov.uk`, `.org.uk`, `.ltd.uk` or
   `.plc.uk`, with placeholders such as `[[EMAIL_1]]`, plus any terms you list in
   `.run\redact_terms.txt`. The patterns are narrow, so ordinary code survives (`std::vector<int>`,
   `a->b`, `0x2A`). Names, project code words and other hostnames or machine names look like ordinary
   text: list them in `.run\redact_terms.txt`. Patient details (names, NHS numbers, dates of birth,
   addresses, clinical notes) and confidential prose aren't caught by the patterns, so when they're on
   the screen, use `local_only` and send nothing ([Sensitive screens](#sensitive-screens)).
2. **Trimming.** Glimmer copies out only the lines the question needs. The excerpt is rebuilt from the
   redacted lines themselves, so it can only contain text that was on the screen. If Glimmer's answer
   can't be used, the whole redacted reading goes (`ask` then reports *not trimmed*). `fix` has no
   question, so it sends every code line on the screen, plus Glimmer's notes about the screen in a
   separate `<screen_notes>` block, redacted the same way. Spark is told both are data, not
   instructions. When Spark plans Fix it, it gets Glimmer's whole description of the window, redacted
   but not trimmed.
3. **A second redaction pass**, with the same placeholders.
4. **Checks**, any of which stops the send: `outbound = "local_only"`; free credits (below); a redacted
   value still present; an inline image (`data:image`); more than `outbound_max_chars`; the logged spend
   reaching `meta_budget_usd`; `.run\outbound.halt` (written when a reply came from an unexpected
   model); and, with `outbound = "approve"`, you declining after seeing the exact text.
5. **Sending.** The exact text is logged, then sent with `store: false` to a Standard model
   (`muse-spark-1.3`; `-contributor` models are refused). The reply must name exactly that model. A
   reply from any other model is discarded and sending halts.

For `nanokvm edit` and `fix`, each later request in the conversation passes the same checks. What `edit`
sends back to Spark about an edit is in words and line numbers only, never screen text. `fix` also sends
the screen as Glimmer read it after each edit, and again for its final check, redacted with the same
placeholders and not trimmed. The `outbound` setting picks the mode: `auto` sends after redaction (the
default), `approve` shows you the exact text and asks first, and `local_only` sends nothing, so
`nanokvm ask` answers with Glimmer alone and `edit` and `fix` get their edits from Glimmer.

**Free credits.** Meta's Billing page says content sent on free credits "may be used for product
improvement". While `meta_billing = "free_credits"`, only test or dummy content may be sent, so the
share prompt of `ask`, `edit` and `fix` also asks you to confirm that the screen shows only such
content (`--stand-in` answers that in advance), and nothing is sent without it. For real content,
switch Billing to pay as you go and set `meta_billing = "paid"`.

**What goes into the locked computer:** no network, no software, no drivers. HDMI carries the picture
*out*, and the only thing that goes back is the monitor identification (EDID) every monitor sends.
Cable 4 carries only keyboard and mouse input. The KVM can send keystrokes but never read them.
`edit` and `fix` type only edits you approve, and `fix` saves only with the same approval. The service
itself accepts keystrokes from any program that can read `.run\token` ([section 10](#10-safety-details)).

Two cables work as physical switches. Unplug cable 4 and nothing can be typed. Unplug cable 3 and the
AI computer can neither see nor type (Monitor A goes dark too). Only Monitor A, the screen wired
through the KVM, is ever captured.

Use this only with the approval of whoever owns the locked computer and its data, for example your
employer or client.

## 3. Wiring for this desk

![Wiring diagram: Locked computer → USB KVM → AI computer, with the five numbered cables](docs/wiring.svg)

**The KVM's ports.** **TARGET** is printed at one end of the top and **HOST**, with a laptop icon, at
the other. **Each end has one full-size HDMI port and one USB-C port**, and the picture flows from
TARGET to HOST.

| End | Port | Side icon | Cable | Goes to |
|-----|------|-----------|-------|---------|
| **TARGET** | HDMI | monitor → | **1** (HDMI) | Locked computer's HDMI port. This is the video **input**. |
| **TARGET** | USB-C | keyboard + mouse | **4** (USB-C → USB-A) | Locked computer's USB-A port, so it sees a keyboard and mouse |
| **HOST** | HDMI | monitor → | **2** (active HDMI → DisplayPort adapter) | Dell Monitor A's DisplayPort input. This is the video **output** (loop-out). |
| **HOST** | USB-C | monitor + keyboard + mouse | **3** (USB-C → USB-C) | AI computer. It carries the video and keyboard/mouse data, and powers the KVM. |
| side | USB-A + T/H switch | – | none | Not used. Leave the switch at **H** (as shipped): a USB stick in this port would then only reach the AI computer, never the locked computer. The switch doesn't affect video, capture or keyboard/mouse. |

> The most likely mistake is plugging the locked computer's HDMI into the **HOST** end. That port is an
> output, so you'd get no picture anywhere. The locked computer always connects to the **TARGET** end.

The two laptops sit on two desks: the locked computer with Monitor A (cables 1, 2 and 4), the AI
computer with Monitor B (cables 3 and 5). Setup steps:

1. **Disconnect the locked computer from its docking station**, if it's connected to one, and from both
   monitors. Plug each laptop into its own charger. Each keeps its own keyboard and touchpad.
2. Connect cables **1, 2 and 3**, and connect Monitor B to the AI computer's other USB-C port through
   the USB-C dock and a DisplayPort cable (**5**). Connect cable **4** only when edits may be typed.
3. Use the monitors' buttons to set **both monitors' input source to DisplayPort**.
4. On the locked computer, use **Extend** mode and put the work you want help with on Monitor A
   ([section 4](#4-locked-computer-settings)).
5. On the AI computer, make sure no display runs on the NVIDIA GPU: on a laptop, use its hybrid
   graphics mode rather than one where the NVIDIA GPU drives the screen. Then
   `nvidia-smi --query-gpu=display_active --format=csv` says *Disabled*, and Muse Glimmer has all of
   the GPU's memory ([section 11](#11-troubleshooting), *VS Code or the desktop stalls*).

Notes:

- **Cable 2 is an active adapter** because the Dell monitors have no HDMI input, and going from HDMI to
  DisplayPort needs an active converter (passive cables only work the other way).
- **Monitor B goes through the dock** (a StarTech.com 116N-USBC-DOCK, which uses DisplayLink) because
  its DisplayPort output needs no adapter and keeps Monitor B off the NVIDIA GPU. A display on that GPU
  takes some of the memory Glimmer needs, and the desktop then stalls while Glimmer runs.
- **Monitor A depends on the AI computer.** The KVM is powered through cable 3, so if the AI computer
  is off or cable 3 is unplugged, Monitor A goes dark. To use the locked computer alone, unplug cable 1
  and plug cable 2's HDMI end straight into the locked computer's HDMI port.
- **Cable 4 is USB-A at the laptop end** because some users found that USB-C to USB-C on the KVM's
  TARGET side doesn't work.

### 3a. Parts list

I used a Sipeed NanoKVM-USB. The code talks to its chips: a MacroSilicon MS2131 for capture, and a
CH9329-compatible keyboard and mouse behind a WCH CH340 or CH343 serial chip (this unit has a CH343).
Another KVM would need the same chips. It came with **no cables**. Ordered on Amazon UK on 2026-10-07:

| # | Ordered | Connects |
|---|---------|----------|
| 1 | Ubluker HDMI cable (48 Gbps), 1 m | Locked computer HDMI → KVM TARGET HDMI |
| 2 | An active HDMI-to-DisplayPort adapter (not part of the 7 Oct order) | KVM HOST HDMI → Dell Monitor A DisplayPort |
| 3 | [Cable Matters USB-IF certified USB-C to USB-C, 10 Gbps](https://www.amazon.co.uk/USB-IF-Certified-Cable-Matters-Delivery/dp/B01L0F6AJI), 1 m | KVM HOST USB-C → AI computer. Must be a USB 3 data cable: it carries the video and powers the KVM. |
| 4 | [UGREEN USB-A to USB-C, USB 3.0](https://www.amazon.co.uk/dp/B08XJP9GMP), 1 m | Locked computer USB-A → KVM TARGET USB-C (keyboard/mouse) |
| 5 | StarTech.com 116N-USBC-DOCK and a DisplayPort cable (not part of the 7 Oct order) | AI computer USB-C → dock → Dell Monitor B DisplayPort |

## 4. Locked computer settings

**Display** (*Settings > System > Display* on Windows 11; for macOS and Linux see
[section 1a](#1a-operating-systems)):

- Use **Extend** mode, so the laptop's own screen and Monitor A show different things. In my tests the
  laptop's own screen was the main display and Monitor A the extended one. Only Monitor A is captured
  and shared with the AI computer: put the work you want help with there, and keep everything else on
  the laptop's own screen, which isn't shared.
- Typed edits (`edit`, `fix`, and code that Fix it hands to the fix loop) use only the keyboard, so
  they work in that setup. Fix it's clicks most likely don't, because the KVM's mouse targets the main
  display. For steps that click, Monitor A would have to be the main display (select it and tick *Make
  this my main display*), which I haven't tested yet.
- Set Monitor A to its **native resolution at 60 Hz**. The KVM accepts up to 4K at 30 Hz and delivers
  1920x1080 to the AI computer, so a 1920x1080 monitor gives Glimmer the sharpest picture.
- If Monitor A is **1920x1200** (16:10), the picture is squashed. Add `display_aspect = "16:10"` to
  `nanokvm.toml`.

**Keyboard layout.** Edits are typed for the layout in `nanokvm.toml`, currently
`keyboard_layout = "uk"`. It must match the locked computer's keyboard layout, assumed to be English
(UK) but not yet checked: see the input indicator on its taskbar, or *Settings > Time & language >
Language & region*. If that laptop uses US, set `"us"`. If it isn't Windows, set `locked_os` too
([section 1a](#1a-operating-systems)).

**Sleep.** A sleeping laptop sends no picture. Wake it before you run a command.

**VS Code for reading code.** Code is small, thin, coloured text, the hardest thing for a video capture.
These are only VS Code settings: open *Ctrl+Shift+P > Preferences: Open User Settings (JSON)* and add:

```jsonc
"editor.fontSize": 16,                // default 14; 16 stays legible in the capture
"editor.minimap.enabled": false,      // more width for code
"editor.wordWrap": "on",              // no code hidden off the right edge
"editor.stickyScroll.enabled": true   // the current class/function name stays visible at the top
```

- **Maximise VS Code on Monitor A,** running at **1920x1080**. While Glimmer reads, hide the side bar
  (**Ctrl+B**) and the panel (**Ctrl+J**). That gives about 40–45 lines per screen.
- **Keep line numbers and the status bar on** (the defaults). Glimmer reads the line numbers, and
  `nanokvm edit` checks the cursor position ("Ln 23, Col 1") on the status bar.
- **Any theme works.** A light one (*Light Modern*) survives video compression best; in dark themes
  thin coloured text is slightly softer.

**VS Code for typed edits (cable 4).** VS Code's typing helpers would garble code typed key by key:
they close brackets, quotes, `/*` comments and HTML or JSX tags, add indentation, wrap a selection
instead of replacing it, generate Doxygen comments, and let Enter, Tab or punctuation accept a
suggestion. Before you run `nanokvm edit` or `fix`, switch them off in the same settings file, and
stop VS Code saving or reformatting by itself. `edit` never saves, and `fix` checks its save by the
tab's unsaved-changes dot, which auto-save would hide. The C/C++ extension's own `[cpp]` defaults win
over global settings, so the main ones are repeated in a `[cpp]` block. I checked these against VS Code
1.144's source on 29 Oct.

```jsonc
"editor.autoClosingBrackets": "never", "editor.autoClosingQuotes": "never",
"editor.autoClosingComments": "never", "editor.autoSurround": "never",
"editor.autoIndent": "none", "editor.formatOnType": false, "editor.trimAutoWhitespace": false,
"editor.acceptSuggestionOnEnter": "off", "editor.acceptSuggestionOnCommitCharacter": false,
"editor.quickSuggestions": { "other": "off", "comments": "off", "strings": "off" },
"editor.suggestOnTriggerCharacters": false, "editor.inlineSuggest.enabled": false,
"editor.wordBasedSuggestions": "off", "editor.unusualLineTerminators": "off", "editor.folding": false,
"html.autoClosingTags": false, "javascript.autoClosingTags": false, "typescript.autoClosingTags": false,
"C_Cpp.doxygen.generateOnType": false,
"files.autoSave": "off", "editor.formatOnSave": false,
"[cpp]": {
  "editor.autoClosingBrackets": "never", "editor.autoClosingQuotes": "never",
  "editor.autoClosingComments": "never", "editor.autoSurround": "never",
  "editor.autoIndent": "none", "editor.formatOnType": false,
  "editor.acceptSuggestionOnEnter": "off", "editor.acceptSuggestionOnCommitCharacter": false,
  "editor.quickSuggestions": { "other": "off", "comments": "off", "strings": "off" },
  "editor.suggestOnTriggerCharacters": false
}
```

One helper has no setting: a line that starts with `}`, `)` or `]` is re-indented to match the line
that opened it. For well-indented code that is what gets typed anyway, and the check after typing
catches any difference.

## 5. Install

Everything lives in `E:\meta_hackathon`, and nothing was installed globally. The one change outside
this folder: on 25 Oct, `llama-server.exe`, `python.exe` and `pythonw.exe` were excluded from Windows
Error Reporting, for this user only, so Windows doesn't collect a crash report that could hold screen
text from their memory. Each is a DWORD set to 1 under
`HKCU\Software\Microsoft\Windows\Windows Error Reporting\ExcludedApplications`. Delete them to undo it.

| What | Location |
|------|----------|
| Python 3.12 (uv-managed) | `.uv\python\cpython-3.12.14-windows-x86_64-none\` |
| uv download/build cache | `.uv\cache\` (set in `uv.toml`) |
| Virtual environment and packages (`opencv-python-headless`, `pyserial`, `numpy`, …) | `.venv\` |
| Code, tests, settings and runtime files (token, keys, logs) | `src\nanokvm_mcp\`, `tests\`, `nanokvm.toml`, `.run\` ([section 12](#12-files-and-testing-without-hardware)) |

**The serial driver.** The KVM's keyboard/mouse channel is a WCH CH343 USB-serial chip (`1A86:55D3`).
When it was first plugged in (22 Oct), Windows used the WCH driver already on this laptop, so nothing
was installed for it. Video works without it. The chip answered at 57600 baud on 22 and 29 Oct.

To rebuild the environment from scratch, run this in PowerShell in this folder. It uses the copy of
`uv` in `%USERPROFILE%\.local\bin` (uv 0.12.5 built the current `.venv`).

```powershell
$env:UV_PYTHON_INSTALL_DIR = "E:\meta_hackathon\.uv\python"
uv python install 3.12.14 --no-bin --no-registry
uv venv .venv --python "E:\meta_hackathon\.uv\python\cpython-3.12.14-windows-x86_64-none\python.exe"
uv sync
```

`E:` is formatted exFAT, which has no symlinks or hardlinks. That's why `uv.toml` sets
`link-mode = "copy"`. During `uv python install`, the message *"Failed to create Python minor
version link directory"* is harmless.

### 5a. Muse Glimmer on the AI computer's GPU (open weights)

Meta's open-weights **Muse Glimmer** (30B, Apache-2.0) runs on the AI computer's own GPU, an RTX 5090
Laptop GPU with 24 GB, through llama.cpp. Glimmer isn't a hosted model, and no screen image goes
anywhere else: the server listens only on `127.0.0.1` and needs the key in `.run\llama_api_key`. I set
up this server before the event (24 Oct) and wrote the code that uses it (`glimmer.py` and the
pipeline) during it. The weights and llama.cpp are git-ignored. Their sources:

| What | Source | Details |
|------|--------|---------|
| llama.cpp | [release b10976](https://github.com/ggml-org/llama.cpp/releases/tag/b10976): `llama-b10976-bin-win-cuda-13.3-x64.zip` and `cudart-llama-bin-win-cuda-13.3-x64.zip`, kept in `tools\downloads\` | build 10976 (commit `987498f45`), Windows CUDA 13.3 build, unzipped into `tools\llama.cpp\` |
| Weights | Unsloth's [unsloth/Muse-Glimmer-30B-GGUF](https://huggingface.co/unsloth/Muse-Glimmer-30B-GGUF) | `models\unsloth\Muse-Glimmer-30B-UD-Q4_K_XL.gguf`, 15,878,222,368 bytes, SHA-256 `82bece304887a313ece08400bc030f6066c7bff5b906b0cd40308ec8a409fd38` |
| Vision projector | the same Unsloth repo | `models\unsloth\mmproj-Muse-Glimmer-30B-BF16.gguf`, 3,849,173,728 bytes, SHA-256 `d08cdcfa0b41d8e20554b52df404ba4f7b440d0bc502a90038508b6407df8ee1` |
| DFlash drafter (speeds up generation) | Meta's [meta-models/Muse-Glimmer-30B-GGUF](https://huggingface.co/meta-models/Muse-Glimmer-30B-GGUF) | `models\dflash-Muse-Glimmer-30B-Q4_K_M.gguf`, 1,631,208,128 bytes, SHA-256 `b2e808bf656086fe86bd0d0bd990f01d33e377537a07c02d45371517c8b264ef` |
| Meta's own weights (`run-glimmer.ps1 -Model meta`) | Meta's [meta-models/Muse-Glimmer-30B-GGUF](https://huggingface.co/meta-models/Muse-Glimmer-30B-GGUF) | `models\Muse-Glimmer-30B-KQuant-17GB-Q4_K_M.gguf`, 16,756,683,904 bytes, SHA-256 `4cc57c0f51040a226e5a72cc47b7613f7772950e460a665f7083de89f183f60e` |
| Meta's vision projector (with `-Model meta`) | the same Meta repo | `models\mmproj-Muse-Glimmer-30B-Q4_K_M.gguf`, 1,400,328,928 bytes, SHA-256 `f48b452316f9b213758e8659444029b961a24a07f99a1abb2a9f88b06f7c00c6` |
| Launcher | this repo | `tools\run-glimmer.ps1`, with the command line below. Sampling is Unsloth's (temperature 1.0, top-p 0.95, top-k 64), and readings of the screen ask for temperature 0 instead, so the same screen reads the same and the checks around typing compare like with like. There's no prompt logging, but an answer that fails to parse is printed in its window, so treat that window like a reading. |
| NVIDIA's compiled-kernel cache | made on first run | `.cache\nv\` |

Start the server, then check it from a second terminal:

```powershell
# Terminal 1: llama-server on http://127.0.0.1:8001 only; Ctrl+C stops it
E:\meta_hackathon\tools\run-glimmer.ps1
# Terminal 2: waits for the server, has Glimmer read a dummy C++ screen, prints accuracy, speed and GPU memory
E:\meta_hackathon\.venv\Scripts\python.exe E:\meta_hackathon\tools\glimmer_smoke.py
```

The launcher refuses to start while another llama-server runs or port 8001 is taken, since requests
could then go to either server. `-NoDFlash` leaves out the drafter: less GPU memory, but slower long
answers.

**Checking that Glimmer runs on this laptop.** `tools\run-glimmer.ps1` starts llama-server with this
command line (one slot, 32K context, all layers on the GPU, images up to 4,096 tokens, no web UI, and
DFlash at draft length 15), using full paths inside `E:\meta_hackathon`. The commands after it let you
see that for yourself:

```
tools\llama.cpp\llama-server.exe -m models\unsloth\Muse-Glimmer-30B-UD-Q4_K_XL.gguf --mmproj models\unsloth\mmproj-Muse-Glimmer-30B-BF16.gguf -np 1 -c 32768 -ngl 999 --fit on -fa auto --load-mode none --image-max-tokens 4096 --host 127.0.0.1 --port 8001 --alias muse-glimmer --no-webui --api-key-file .run\llama_api_key --cors-origins localhost --no-cors-credentials --no-slots --cache-ram 0 --ctx-checkpoints 0 --offline --temp 1.0 --top-p 0.95 --top-k 64 --spec-type draft-dflash -md models\dflash-Muse-Glimmer-30B-Q4_K_M.gguf -ngld 999 --spec-draft-n-max 15
```

```powershell
Get-FileHash E:\meta_hackathon\models\unsloth\Muse-Glimmer-30B-UD-Q4_K_XL.gguf, E:\meta_hackathon\models\unsloth\mmproj-Muse-Glimmer-30B-BF16.gguf, E:\meta_hackathon\models\dflash-Muse-Glimmer-30B-Q4_K_M.gguf   # the SHA-256 values above
E:\meta_hackathon\tools\llama.cpp\llama-server.exe --version   # version: 0.4.1-dev (build 10976, commit 987498f45)
nvidia-smi                                                     # while it runs: llama-server.exe, about 21.7 GB
Get-NetTCPConnection -LocalPort 8001 -State Listen             # LocalAddress 127.0.0.1 only
```

**Measured on this laptop.** Before the event (24 Oct), with `tools\glimmer_smoke.py` on dummy C++
rendered at 1920x1080, Glimmer read all 27 of 27 lines right (compared with runs of spaces collapsed, so
indentation wasn't checked). A full-screen read took 6.7–7.0 s (image prompt about 5 s, generation
131–141 tokens/s) and used about 23 GB of the GPU's 24 GB with the drafter on. On the real capture
(29 Oct, on the stand-in laptop), from 1920x1080 frames of 2,691 image tokens, Glimmer transcribed a
9-line C++ file in VS Code exactly (checked by eye), and `read --describe` named the app, the file and
the status bar's contents correctly. That took 6.1 s and 6.7 s with the drafter (21.7 GB, with the
NVIDIA GPU driving no display), or 7.1 s and 13.5 s without it (20.9 GB). It's that fast only on the
full-power charger, in the highest performance mode, with no display on the NVIDIA GPU
([section 3](#3-wiring-for-this-desk), setup step 5; *Glimmer is slow* in [section 11](#11-troubleshooting)).

### 5b. Muse Spark through Meta's Model API

Muse Spark runs in Meta's cloud. Save your Model API key as the only line of `.run\meta_api_key`
(git-ignored; it's never printed or logged), then check the account with dummy text:

```powershell
E:\meta_hackathon\.venv\Scripts\python.exe E:\meta_hackathon\tools\spark_smoke.py
```

It lists the models the key can use, sends three dummy chat requests with `store: false` (plain,
strict JSON and streaming), and checks that each reply came from exactly `muse-spark-1.3`. I wrote this
smoke test before the event. The client the pipeline uses (`src\nanokvm_mcp\spark.py`, written during
it) uses Chat Completions with streaming, refuses redirects, sends text only, and accepts only
Standard model IDs.

## 6. Using it: read, ask, edit and fix

Start Muse Glimmer first, in its own terminal
([section 5a](#5a-muse-glimmer-on-the-ai-computers-gpu-open-weights)), with no display on the NVIDIA GPU
([section 3](#3-wiring-for-this-desk), setup step 5). Then, in another terminal (prefix each command
with `E:\meta_hackathon\.venv\Scripts\`):

```powershell
nanokvm read                                   # transcribe the code on the screen, with line numbers
nanokvm read --describe                        # describe what is on the screen
nanokvm read --errors                          # list the compiler errors and warnings on the screen
nanokvm ask "why does sum_first overrun?"      # Spark answers from the redacted screen text
nanokvm edit "fix the off-by-one in sum_first" # Spark proposes edits; each one you approve is typed in
nanokvm fix                                    # no request: Spark finds the error on the screen and fixes it
```

Each command first checks that Glimmer answers, and shares nothing if it doesn't. Then it asks you at
the keyboard to share the screen with Muse Glimmer for a few minutes (`--minutes`: 5 by default, 10 for
`edit` and `fix`), and the share ends when the command does. There's deliberately no `--yes`, and
nothing is approved without a terminal, so a script, or an AI running commands without one, can't
approve. That guards against mistakes, not against programs on this computer: anything that can type
into the terminal running `nanokvm` can answer its prompts, and a program that can read `.run\token`
can use the service without the command ([section 10](#10-safety-details)). So don't let an AI drive
that terminal or click in the viewer, whose prompt box asks the same questions in its window.
`--region x,y,w,h` reads only part of the screen, in the coordinate space `nanokvm status` shows as
*Screen*.

- **`read`** keeps everything on the AI computer. It prints the text, how long Glimmer took, and a
  warning if the picture was uniform (no signal, a sleeping display). For code, it also says which line
  numbers it read and whether any are missing. Like `ask` and `edit`, it refuses a frame more than 2 s
  old, since the video may have stopped.
- **`ask`** prints Spark's answer with the placeholders restored, then what was redacted, whether
  Glimmer trimmed the text, the cost and where it's logged. `--stand-in` answers the free-credit
  question in advance ([section 2](#2-what-leaves-the-ai-computer)), `--approve` shows the exact text
  before sending (a flag can make `outbound` stricter, never looser), and `--local-only` has Glimmer
  answer instead and sends nothing.
- **`edit`** works like `ask`, but Spark answers with `apply_edit` calls: replace lines S to E with new
  lines. With `--local-only` (for `edit` and `fix`), Glimmer gets the same request, instructions and
  `apply_edit` tool through llama-server's tool calls, and its edits are checked, shown and typed the
  same way. Nothing is sent, so nothing is redacted or trimmed.
- **`fix`** is `edit` with no request. Glimmer also notes what is wrong in the VS Code window (the
  file's name only, squiggles, its Problems and terminal panels, the status bar's counts) and shows you
  the notes. It's told to leave out other apps and the system's notifications, an instruction rather
  than a crop. Spark gets the notes with every code line on the screen, untrimmed, finds the error
  itself, and after each typed edit sees the screen as Glimmer read it. When Spark says it's done,
  Glimmer reads the screen afresh and Spark checks every line for anything the edits broke, at most
  twice a run. Once Spark answers *FIXED*, `fix` asks *Save … in VS Code on the locked computer?*
  ([how it saves](#saving-after-the-fix)).
- **`--auto`** (for `edit` and `fix`) types the edits without asking for each one. You allow it once
  per run: the share prompt then also asks to type without asking and, for `fix`, to save the file once
  Spark has checked the fix on the screen. Each edit is still shown, the screen is checked before and
  after typing, the first failed check stops the run, and Ctrl+C stops it at once. Ctrl+Z in VS Code
  still undoes the edits after a save. Check that the locked computer's owner is happy with edits typed
  and saved this way. How this mode fits Meta's usage policy is in
  [LEARNINGS.md](LEARNINGS.md#why-a-usb-kvm). On free credits the same yes confirms that the screen
  shows only test or dummy content, unless `--local-only` means nothing is sent.

**Checking what Glimmer reads.** To measure its accuracy on the live screen, type the 10-line dummy
file `tools\ab_screens\live_check.cpp` into VS Code on the locked computer. It must match the file
exactly, so first set `"editor.autoClosingBrackets": "never"`, `"editor.autoIndent": "none"`,
`"editor.acceptSuggestionOnEnter": "off"` and `"editor.inlineSuggest.enabled": false` (or all of
section 4's typed-edit settings). Open a new empty window (Ctrl+Shift+N) so no work is on the screen,
press Ctrl+N, and choose Plain Text (Ctrl+K M) so VS Code can't switch the tab to C++ while you type.
Type the file with the space bar for the spaces, and press no Enter after the last `}`. Then press End,
then Home, on each line: the status bar should show *Col 5* on lines 5, 6, 8 and 9, *Col 9* on line 7
and *Col 1* on the others (Home alone jumps to column 1 when the cursor is already at the indent). Set
the language to C++ (Ctrl+K M) so it's coloured like real code, and run
`nanokvm read --check tools\ab_screens\live_check.cpp` (it finds the file from any folder). It lists by
line number which lines Glimmer read exactly, which differ only in spaces, indentation or text, and
which it missed. It never repeats the text read, so that list can be shared, but the transcription
printed above it can't.

**How `nanokvm edit` types an edit.** Each `apply_edit` call is checked on the AI computer first. The
placeholders are restored and none may be left, nor any tab. Every character must be typeable on the
keyboard layout, an edit has at most 40 lines and 2,000 characters, and its lines must be on the screen
and read unambiguously (rows whose numbering looks wrong are left out). Only the first edit in each
reply from Spark is used. You then see the diff with the file's line numbers, the exact keys and
Spark's note, and answer *Type this edit? [y/N]*.

Typing is keyboard only, by line number, and each key that could change the file waits for a check on
the screen ([LEARNINGS.md](LEARNINGS.md#typing-blind) has the reasoning). No digit or Enter goes out
until Glimmer sees VS Code's Go to Line box, because in VS Code's terminal Ctrl+G is *Go to Recent
Directory* and Enter would run a `cd`. The status bar must then say *Ln S, Col 1* with nothing selected,
and the lines must read as the ones Spark changed. End and Home on each old line measure its
indentation in the status bar's *Col*, since Glimmer can read it a few spaces off. If it differs and
Spark's new line doesn't match it, nothing is typed and Spark plans again with the measured numbers
(`fix`; `edit` stops). Ctrl+L's selection must end exactly where planned before Backspace deletes it.
An edit that ends on the last line shown is typed only if that's the file's last line, and a line that
goes on below the screen, unread, is never deleted. After typing, the status bar and a fresh reading
must show the new lines as typed, though runs of spaces inside a line aren't compared.

`fix` also asks Spark to keep its changes indented like the code around them, and its final check looks
for misindented lines. If an edit fails the first checks, or you decline it, nothing is typed: Spark is
told why, in words and line numbers, and may propose another. A failed check while typing stops the
session, except in `fix` when the lines no longer read as Spark's (Glimmer can read a line differently
the second time) or are indented differently: Spark then gets the screen as read, with the measured
indentation, and may try again. Before Backspace, only navigation keys have gone out and the file is
unchanged. After it, check the file. A session allows at most 4 edits and 8 requests to the coder
(Spark, or Glimmer with `--local-only`). **Ctrl+C** in the terminal stops the typing within about a
quarter of a second and releases every key.

<a id="saving-after-the-fix"></a>**Saving after the fix (`fix` only).** Once Spark answers the fresh
check with *FIXED*, and you allow it (asked then, or with `--auto` at the share prompt):

1. Glimmer reads VS Code's selected tab. It must be the file Glimmer named in its notes, and should
   show the unsaved-changes dot. An untitled file isn't saved, since Ctrl+S would ask where to save it.
2. Escape, Ctrl+G and the Go to Line check above make sure the keys go to VS Code (Ctrl+S in another
   app could save something else there). Then Escape closes the box.
3. Ctrl+S (Cmd+S on a Mac), then Glimmer reads the tab again: the dot must have gone. If it still
   shows, nanokvm says so and sends Escape, in case a *Save As* dialog opened.

If Spark doesn't answer *FIXED*, or its last edit wasn't checked on a fresh reading, the file isn't
saved and the run says why. Ctrl+Z in VS Code still undoes the edits after the save (save again after).

**Before running `edit`:** plug in cable 4, show the file in VS Code on the locked computer in a single
editor group (no split editor or diff view) with its editor focused, switch the typing helpers off
([section 4](#4-locked-computer-settings)), and **save the file**. Undoing typed code takes many Ctrl+Z
presses, while *File: Revert File* returns to the saved version at once. Ask for small, targeted edits,
since typing runs at about 20 characters a second. While it types, leave the locked computer's own
keyboard and mouse alone: Windows shares Shift, Ctrl and Alt between keyboards, and a click moves the
focus. Keep Caps Lock off too. It's shared as well, and typing allows for it only when the KVM reports
it.

### 6a. The whole loop, step by step

These are the steps behind the `fix --auto` runs on the stand-in laptop that found and fixed deliberate
errors on 29 and 30 Oct and, on 30 Oct, saved the fixed file.

**On the AI computer, once per session**

1. **Power and GPU.** Plug in the laptop's full-power charger, choose its highest performance mode, and
   keep every display off the NVIDIA GPU: `nvidia-smi --query-gpu=display_active --format=csv` must say
   *Disabled* ([section 3](#3-wiring-for-this-desk), setup step 5).
2. **Keep the readings private.** Close other apps that can capture this screen (other AI assistants,
   screen recorders), and run the commands below in PowerShell windows outside VS Code, where the
   readings print.
3. **Load Muse Glimmer on the GPU** in window 1, and keep that window open. It's ready when it shows
   `model loaded` and `listening on http://127.0.0.1:8001` (about 10–30 s with the files cached, about
   72 s from cold), and `nvidia-smi` then shows `llama-server.exe` using about 21.7 GB
   ([section 5a](#5a-muse-glimmer-on-the-ai-computers-gpu-open-weights)):

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File E:\meta_hackathon\tools\run-glimmer.ps1
   ```
4. **Start the nanokvm service** in window 2 with `E:\meta_hackathon\.venv\Scripts\nanokvm.exe start`.
   It opens the KVM's capture device, saves no picture, and hands frames to Glimmer only during a share
   you approve. A few seconds later `nanokvm status` shows `Video: streaming 1920x1080 MJPG ... 30 fps`
   and `Sharing: 0 ...`. With cable 4 plugged in, `nanokvm keytest` checks that the locked computer
   takes the KVM's keys.

**On the locked computer**

5. Show the file in VS Code in one editor group, with the typing helpers and auto-save off
   ([section 4](#4-locked-computer-settings)), and save it. Turn on Do not disturb and keep Caps Lock
   off. `keyboard_layout` in `nanokvm.toml` must match its keyboard layout. Click into the file so VS
   Code has the focus, then leave the locked computer's keyboard and mouse alone.

**Read, then fix** (in window 2; type `y` at the share prompt)

6. **See what Glimmer reads:** `nanokvm read` transcribes the code with its line numbers, and
   `nanokvm read --describe` describes the screen. Neither sends anything.
7. **Let Spark fix it** with `E:\meta_hackathon\.venv\Scripts\nanokvm.exe fix --auto`. One `y` shares
   the screen, lets the edits be typed without asking for each one, and lets the file be saved once
   Spark has checked the fix (on free credits it also confirms the screen shows only test or dummy
   content). Then, on its own: Glimmer reads and diagnoses the screen → Spark proposes an edit →
   the KVM types it, with Glimmer checking the screen before and after → Spark sees the screen as it now
   is → when Spark says it's done, Glimmer reads the screen afresh and Spark checks the fix there → on
   *FIXED*, Ctrl+S, with Glimmer checking the file's tab before and after. It stops when the fix is
   saved, at the first failed check, or at the limits (4 edits, 8 requests to Spark). Ctrl+C stops it
   at once. Leave out `--auto` to approve each edit and the save. With patient or confidential data on
   the screen, use `fix --local-only`: plain `fix` sends redacted text, and redaction can't catch that
   data ([Sensitive screens](#sensitive-screens)).
8. **Review.** The last lines say whether the file was saved and how that was checked. Look at the
   change in VS Code. Ctrl+Z still undoes it (then save again).
9. **Finish:** Ctrl+C in window 1 stops Glimmer, and `nanokvm stop` stops the service.

## 7. Watching the screen yourself, and asking about it

Run `E:\meta_hackathon\.venv\Scripts\nanokvm.exe viewer`. A window on the AI computer shows the
locked computer's screen live, at up to 30 fps (`--fps` changes it), with a prompt box beside it
([below](#asking-from-the-viewer)). Two status lines under the picture show the capture mode, the
capture and window frame rates and the delay from capture to the window, the keyboard/mouse COM port
and, once an action has opened the link, whether the KVM reports cable 4 as connected. They say
*Screen sharing: off*, or that Muse Glimmer on this laptop can read it during a share for
`nanokvm read`, `ask`, `edit`, `fix` or the prompt box. They warn if the picture is uniform (no HDMI
signal, a sleeping display or a blank screen), and say *shown at 75%* (for example) when it's scaled
down to fit: press **1** for actual pixels, so small text is as sharp as the capture.

The live view itself is read-only. Keys and clicks in it stay on the AI computer, it never makes the
service open the keyboard/mouse link, and it saves nothing to disk. Only **Fix it** in the prompt box
clicks or types on the locked computer, after your approval. It's an ordinary window, though. Anything
that captures the AI computer's screen (a screen share in a call, a screen-capture tool, or a command
an AI runs) captures the locked computer's screen too, and the answers beside it. Close it before you
let an AI work on the AI computer unattended.

Keys: **F11**, or a double-click on the picture, for full screen (**Esc** leaves it), which hides the
status lines and the prompt box so a 1920x1080 screen shows the picture at actual size. **1** switches
between fitting the window and actual pixels (drag to pan), **R** starts the service if it has stopped,
**Q** (or Ctrl+C in the terminal) closes the window, and **F2** hides or shows the prompt box. While the
prompt box has the focus, letters are typing: click the picture to use 1, R and Q again. On closing,
the viewer prints how many frames it showed and the delay.

<a id="asking-from-the-viewer"></a>**Asking from the viewer.** Type a question about what the locked
computer shows, such as *why won't this compile?* or *what does this error mean?*, and press Enter
(Shift+Enter starts a new line). Nothing is read or sent before you ask. Then:

1. **The first question asks, in the panel, to let Muse Glimmer read the screen for 30 minutes**,
   and asks again when that share ends or the service loses it (Fix it asks again when fewer than 10
   minutes are left). On free credits, each of those questions also asks you to confirm the screen
   shows only test or dummy content before anything can go to Spark.
2. **Glimmer reads** the window in front. It's asked for the program, what it shows, and every message
   in it, quoted exactly, and to leave out other windows, the taskbar and the system's notifications.
   That is an instruction to the model, not a crop: Glimmer is shown the whole screen. With *the code*
   chosen, it transcribes the code on the screen with its line numbers.
3. **Muse Spark answers** from that reading, redacted and trimmed, through the same outbound gate as
   `nanokvm ask`. Under the answer it shows what was sent and redacted, and the cost. Tick **Local
   only** and Glimmer answers instead, with nothing sent. Use it whenever patient or confidential data
   could be anywhere on the screen. It also keeps Fix it on the AI computer. Tick **Show me the exact
   text before it is sent** to approve each send. Both can only make `nanokvm.toml`'s `outbound`
   stricter: if it says `local_only` or `approve`, the box is ticked and fixed.

<a id="fix-it"></a>**Fix it: a task in any program.** Describe a task instead, such as *copy the Systems
Requirements module into the other project's Requirements folder*, and press **Fix it**. It's done one
step at a time: a click, double-click or right-click on something on the screen, text typed into what
has the focus, keys such as Ctrl+C or Enter, or a scroll up or down.

1. **Muse Glimmer looks at the screen and plans the next step, on this laptop.** By default nothing
   leaves it, not even text. Tick **Fix it: Spark plans and fixes code** (with Local only unticked) to
   let Muse Spark plan instead, from Glimmer's redacted description of the window in front, through
   the outbound gate. Spark names targets in words.
2. **Glimmer finds the step's target on the screen**, here, on its own 0-1000 scale, and the viewer
   outlines it in orange on the live picture (`tools\glimmer_point.py` checks this on a synthetic
   screen: see the results table). With *NONE*, or an answer that isn't just a box, nothing is clicked
   and the planner is told. Pictures and screen positions never leave this laptop.
3. **You allow the step** (*Do this step*, or *Stop here*), and the KVM does it. With **Auto** ticked,
   one question at the start names the task and covers its steps, each shown with its target outlined
   1.5 s before it's done, so Stop still catches it. A step that types text, or changes something,
   always asks: the planner says it changes something (anything but a plain "no" counts), or its keys
   are Enter, Delete, Backspace, Ctrl with X, V, S, Z, Y, D or W, Alt+F4 or Shift+Insert (whatever the
   planner calls them). A typing step's question says where the cursor is when the editor's status bar
   shows it (*Ln 8, Col 1*).
4. **Glimmer looks again**, and the next step starts from the screen as it is now. The log keeps each
   step. It ends when the planner says the task is done (check it on the screen) or it's stuck, after
   20 steps, or at Stop.

**A change to code goes to `fix`'s loop, not to clicks and typing.** On the stand-in laptop a click
into code once missed the line it aimed at, most likely because Monitor A wasn't the main display
([LEARNINGS.md](LEARNINGS.md#typing-blind)). So when the task is a change to code shown in an editor
with line numbers, the planner is told to hand it to `fix`'s loop
([section 6](#6-using-it-read-ask-edit-and-fix)) instead of clicking and typing, and that ends its part
(ask again for anything after the change). Your task becomes the coder's request: it changes only what
you asked, and a task that asks what is wrong, or to fix it, gets the usual search for the error. Each
edit appears in the panel with its diff and keys, is typed only when you click *Type this edit*, and
is checked on the screen. Once the coder answers *FIXED* on a fresh reading (the task done, the file
sound), *Save* saves the file, checked as `fix` checks it. The coder is the planner's model: Glimmer by
default, with the instructions and `apply_edit` tool Spark would get, so nothing is sent, or Spark when
*Spark plans and fixes code* is ticked. With **Auto**, one more question before the first edit covers
the typing and the save (answer *Ask for each edit* to approve each edit and the save). The hand-over
is the planner's choice, not a guarantee, which is why a typing step always asks and says where the
cursor is. Before Fix it on code, click into the file in VS Code on the locked computer, as for `fix`.

Refused outright: keys that reach outside the program (the Windows key by any name, Ctrl+Esc, Alt+Tab,
Alt+Esc, Ctrl+Alt+Delete; Cmd stays allowed on a Mac), a character the keyboard layout can't type, and
a target box bigger than half the screen. Text on the screen is data, never instructions, but a
planner can still be misled by it. So read each step before you allow it, above all in tools that
change shared data, and try a task on test data first. Fix it takes its task only from the prompt box.

Approvals appear in the panel beside the live picture, so you can watch the screen while you decide,
and one that arrives in full screen or with the panel hidden brings the panel back. Their buttons work
only a moment after the approval can be seen, so a click or key meant for something else can't answer
them, and a long one scrolls and says so in its title. **Stop** ends the work at the next step and
releases every key, as Ctrl+C does in the terminal, and so does closing the window. Fix it is off when
`control = false`.

## 8. First run with the hardware

Wire everything as in [section 3](#3-wiring-for-this-desk), then run:

```powershell
E:\meta_hackathon\.venv\Scripts\nanokvm.exe diag              # devices, video and the service (saves nothing)
E:\meta_hackathon\.venv\Scripts\nanokvm.exe diag --mouse-test # also moves the locked computer's pointer in a small square (needs cable 4)
```

`diag` finds the KVM's capture device by its USB ID (`345F:2131` or `345F:2133`; your webcam is listed
but never opened) and the keyboard/mouse port by the USB IDs in `serial_ids` (this unit: CH343,
`1A86:55D3`). It starts the service, waits up to `--wait` seconds (10 by default) for video, and warns
if the picture is uniform or the newest frame is more than 2 s old. It takes no picture and saves
nothing. Plain `diag` doesn't open the keyboard/mouse link, so it doesn't test cable 4: run
`diag --mouse-test` and check that the locked computer's pointer moves. After that, `nanokvm status`
shows `'target_usb_connected': True` if the KVM reports cable 4 as connected. `diag` ends with
`RESULT: OK` or a list of problems with fixes. Then run `nanokvm viewer` and check the picture by eye
([section 7](#7-watching-the-screen-yourself-and-asking-about-it)).

Other commands (prefix with `E:\meta_hackathon\.venv\Scripts\`):

```powershell
nanokvm status          # service, video, keyboard/mouse and screen shares (--json for the raw data)
nanokvm keytest         # presses Num Lock twice: does the locked computer take the KVM's keys? (no picture)
nanokvm viewer          # live view in a window, with a prompt box to ask about the screen or fix it (section 7)
nanokvm start | stop    # the service also starts automatically when a command needs it
nanokvm serve           # run the service in the foreground with console logging (debugging)
```

Apart from `keytest` and the `viewer` window, these commands were built before the event, and some
gained small changes during it. Unlike `read`, `ask`, `edit`, `fix` and the viewer's prompt box,
`diag` and the viewer's live picture don't ask to share the screen: they need only the token in
`.run\token`.

## 9. Settings (`nanokvm.toml`)

**The Meta-model pipeline:**

| Setting | This setup | Effect |
|---------|------------|--------|
| `outbound` | `"auto"` (default) | `"auto"` sends redacted text to Spark; `"approve"` shows it and asks first; `"local_only"` sends nothing ([section 2](#2-what-leaves-the-ai-computer)) |
| `meta_billing` | `"free_credits"` (default) | `"free_credits"`: only test or dummy content may go to Meta, confirmed at the share prompt or with `--stand-in`; `"paid"`: real content, once Billing is pay as you go |
| `spark_model` | `"muse-spark-1.3"` | A Standard model ID; `-contributor` IDs are refused |
| `spark_reasoning` | `"medium"` | Spark's `reasoning_effort`: `minimal`, `low`, `medium`, `high`, `xhigh` or `max` |
| `spark_max_tokens` | 8000 | `max_completion_tokens`, which includes Spark's reasoning |
| `meta_budget_usd` | 25.0 | Stop sending once the audit log adds up to this much |
| `outbound_max_chars` | 20000 | Refuse to send more text than this in one request |
| `glimmer_port` / `glimmer_timeout_s` | 8001 / 600 | The local llama-server (always `127.0.0.1`) and how long to wait for it |
| `read_reasoning` | `"low"` | Glimmer's reasoning level for reading the screen (Meta's paper shows the same accuracy at low) |
| `minimise_reasoning` / `minimise_think_tokens` | `"high"` / 1024 | Glimmer's reasoning level, and its thinking cap, when trimming text before it leaves |

**The KVM and the service:**

| Setting | This setup | Effect |
|---------|------------|--------|
| `control` | `true` | Keyboard/mouse on. `false` = view-only: `nanokvm edit` and `fix` refuse, Fix it in the viewer is off, and the service refuses keystrokes from any program. |
| `keyboard_layout` | `"uk"` | The locked computer's keyboard layout, for typed edits: `"uk"` or `"us"`. These are PC layouts; on a Mac use `"us"` ([section 1a](#1a-operating-systems)). |
| `locked_os` | `"windows"` (default) | The locked computer's OS: `"windows"`, `"macos"` or `"linux"` ([section 1a](#1a-operating-systems)). |
| `display_aspect` | not set | `"16:10"` if Monitor A is 1920x1200 and the picture looks squashed |
| `viewer_width` / `viewer_quality` | 1920 / 90 | Sharpness and JPEG quality of the viewer window's picture. Its frame rate is `nanokvm viewer --fps`. |
| `capture_fourcc` | `"MJPG"` | Try `"YUY2"` (uncompressed, sharpest code text) if `nanokvm diag` lists the KVM as *USB3 Video*. |
| `serial_port` | auto | Force a COM port, for example `"COM5"` |
| `serial_ids` | `"1A86:7523,1A86:55D3"` | USB IDs that identify the KVM's serial port; this unit's chip is a CH343 (`1A86:55D3`) |
| `idle_exit_min` | 60 | Stop the background service after this many idle minutes (0 = never) |
| `port` | 8765 | Port of the service, always on `127.0.0.1`. Change it if another program uses 8765, but run `nanokvm stop` before you edit it. |
| `capture_width` / `capture_height` / `capture_fps` | 1920 / 1080 / 30 | Video mode requested from the KVM |
| `region` | not set | Manual active area `"x,y,w,h"` in capture pixels. Normally leave it unset. A `.run\calibration.json` left from an earlier calibration still sets the area; delete it to use the whole frame. |
| `shot_width` | 1456 | Width of the pointer coordinate space (Fix it's clicks, `--region`; `nanokvm status` shows it as *Screen*). Glimmer reads full-resolution frames. |
| `key_hold_ms` / `key_gap_ms` / `click_hold_ms` | 25 / 15 / 60 | Keyboard and click timing in milliseconds. Raise them if the locked computer misses keys; typing gets slower. |
| `wake_after_s` | 60 | Before the first key or click after this many seconds without any (or after the keyboard link opens), the service taps Scroll Lock until the locked computer's Scroll Lock light follows, then taps it back. Keys sent before then could be lost. Scroll Lock changes nothing in VS Code or in typing. 0 turns it off. |

`src\nanokvm_mcp\config.py` lists every setting, with its default. Any setting can also come from an
environment variable `NANOKVM_<NAME>`, for example `NANOKVM_PORT`, which overrides `nanokvm.toml`.
After editing, run `nanokvm stop`. The service restarts when it's next needed.

## 10. Safety details

- **Nothing is typed or clicked without your yes**, given for each edit or once per run with `--auto`,
  and for each Fix it step or once per task with Auto (a step that types or changes something still
  asks). Spark and the planner only propose; neither types anything itself. Glimmer checks the screen
  before each irreversible step and after the typing, and `fix` saves only after the coder has
  answered *FIXED* on a fresh reading ([section 6](#6-using-it-read-ask-edit-and-fix), [section 7](#fix-it)).
- **No stuck keys, one action at a time.** Every action ends with all keys and buttons up, even when
  something fails, and releasing never moves the pointer. Ctrl+C during `nanokvm edit` stops the typing
  within about a quarter of a second. If two programs try to type at once, the second gets "busy",
  never queued to run later into whatever window has focus by then. Status requests never open the
  keyboard/mouse link, because opening it sends an all-keys-up report to the locked computer.
- **Only the KVM's COM port is used.** It's auto-picked only when the KVM itself is attached and
  exactly one port matches `serial_ids` (this unit: CH343, `1A86:55D3`). If several match (Arduinos
  often use the same chips), it picks none and asks you to set `serial_port`.
- **Screen text is untrusted.** Glimmer's and Spark's instructions say that screen text is data, not
  instructions. That lowers, but doesn't remove, the risk of prompt injection (Meta reports attacks
  on Glimmer succeeding 28.4% of the time), which is one reason every edit and step is shown to you
  before it's done. Screen text quoted to Glimmer as the coder can't carry its chat control tokens
  (`<|eom|>` and the like become look-alikes). Text from the models is printed with control characters
  shown as escapes, so it can't move the cursor or hide what the terminal shows.
- **The local services are reachable only from this laptop.** The nanokvm service listens only on
  `127.0.0.1`, rejects foreign Host headers, and requires the random token in `.run\token`, sent in a
  request header and never in a URL, so web pages can't read the screen or type. Glimmer's server also
  listens only on `127.0.0.1` and needs its own key. Neither is locked down against programs on this
  laptop yet: any program that can read the token, kept in `.run\` with the keys, can use the service.
  To replace the token, run `nanokvm stop` and delete `.run\token`. The next start makes a new one.

## 11. Troubleshooting

| Symptom | Fix |
|---------|-----|
| No signal / black picture (`nanokvm status` says *no_frames*, or `nanokvm status --json` shows `"blank": true`) | Check that the locked computer's HDMI goes into the **TARGET** end. Wake the locked computer. Check Monitor A is enabled in the locked computer's display settings ([section 4](#4-locked-computer-settings)). |
| Monitor A is black but capture works, or the other way round | Set Monitor A's input to **DisplayPort**, and check the HDMI-to-DisplayPort adapter is plugged in at both ends (and into USB power, if it has a power lead). Check Monitor A runs at its native resolution at 60 Hz ([section 4](#4-locked-computer-settings)). |
| `USB KVM capture device not found` | Check cable 3, then run `nanokvm diag`. If the KVM gets no power over USB-C to USB-C, use USB-A to USB-C into the AI computer. |
| `video capture needs Windows on the AI computer` | Capture runs only on a Windows AI computer, for now. This limit doesn't apply to the locked computer ([section 1a](#1a-operating-systems)). |
| `COM port not found` (video works) | Run `nanokvm diag`. It lists every COM port. This unit's chip is a CH343 (`1A86:55D3`, shown as *USB-Enhanced-SERIAL CH343*, COM5 so far) and needs no extra driver. Check that `serial_ids` in `nanokvm.toml` still includes `1A86:55D3` (the built-in default looks only for `1A86:7523`), then reseat cable 3 or try another USB-C port: video and the serial chip use different wires in the cable. Also look for a disabled or yellow-flagged entry under *Ports (COM & LPT)* in Device Manager. |
| `COMx is in use` or `no video` | Close the KVM's own app or web page, or any serial terminal: only one program can hold the device. Run `nanokvm stop` before opening that app. |
| `Muse Glimmer isn't ready at http://127.0.0.1:8001` or `Glimmer isn't answering` | Start `tools\run-glimmer.ps1` and wait until its window shows `model loaded` and `listening on http://127.0.0.1:8001` (about 10–30 s with the model files cached, about 72 s from cold). |
| `no Meta API key` | Save the key as the only line of `.run\meta_api_key` ([section 5b](#5b-muse-spark-through-metas-model-api)). |
| `the Meta account is on free credits` | Only test or dummy content may go: say yes to the share prompt's question about it, or add `--stand-in`. For real work, switch Billing to pay as you go and set `meta_billing = "paid"` ([section 2](#2-what-leaves-the-ai-computer)). |
| `no numbered code lines on the screen` | Show the file in VS Code with line numbers on, maximised on Monitor A. |
| `Not applied: after Ctrl+G, Glimmer saw no box at the top of the screen` (or *a box … but not VS Code's Go to Line box*) | Only Escape, Ctrl+G and Escape were sent. The first keys after the service starts or sits idle could be lost, so the service now first taps Scroll Lock until the locked computer's light follows (`wake_after_s`, [section 9](#9-settings-nanokvmtoml)). If the action's result says the light didn't follow, run `nanokvm keytest`. Otherwise click into the file in VS Code on the locked computer (not its terminal or another app) and try again. |
| `Not applied: after Go to Line the status bar shows …` | Nothing was typed except Go to Line. Check that the status bar is on (*View > Appearance > Status Bar*) and try again. |
| `Not applied: after selecting, the status bar shows …` | Nothing was deleted: Escape dropped the selection. Check that folding is off ([section 4](#4-locked-computer-settings)) and that nothing else has the keyboard, then try again. |
| `Spark's edit … can't be typed: line N can't be edited from this reading` | Glimmer didn't read that line cleanly: it's off the screen, or its numbering looked wrong. Spark is told and may try again. If it keeps happening, scroll so the lines sit mid-screen and ask again. |
| `line N is the last line on the screen and the file goes on below it` | Nothing was deleted: Escape dropped the selection. Scroll so that line sits higher on the screen and run it again. |
| `the screen no longer shows the expected lines at line N` | Nothing was typed but Go to Line. With `fix`, Spark gets the screen as read then and tries again. With `edit`, run it again. |
| `the editor indents line N: K spaces …, not as the screen text showed` | Nothing was typed: Glimmer read that line's indentation wrong, and Spark planned on it. With `fix`, Spark gets the measured indentation and tries again. With `edit`, run it again. |
| `line N is indented by K spaces in the editor, not M as typed`, or `after typing, the status bar shows … not Ln N, Col 1` | Something re-indented the typed text: switch `editor.autoIndent` off ([section 4](#4-locked-computer-settings)), then check the file (Ctrl+Z undoes it). |
| `Not saved (…)` or `Not saved: Ctrl+S sent, but …` | The run says why: Spark didn't answer *FIXED*, the selected tab isn't the file Glimmer named, VS Code didn't have the keys, or the tab still showed unsaved changes after Ctrl+S (look for a message in VS Code). Check the change and save it yourself. `Probably saved` means Glimmer saw no unsaved-changes dot even before Ctrl+S (is auto-save on?). |
| `typed, but the screen doesn't show the expected lines` | Check the file on the locked computer. *File: Revert File* returns to the last saved version, while Ctrl+Z undoes typing only in small steps. Check that the typing helpers are off ([section 4](#4-locked-computer-settings)) and that `keyboard_layout` matches. |
| Wrong characters typed (for example `"` and `@` swapped) | `keyboard_layout` doesn't match the locked computer's keyboard layout (on a Mac, see [section 1a](#1a-operating-systems)). Fix it and run `nanokvm stop`. |
| Keyboard does nothing, or `nanokvm status` shows `'target_usb_connected': False` | Run `nanokvm keytest`. It presses Num Lock twice and checks that the locked computer's Num Lock follows, with no picture taken. If it says *Keyboard OK*, the keys arrive, and an edit that stops at *Go to Line* means VS Code didn't have the focus: click into the file first. Otherwise check cable 4 and try the locked computer's other USB-A port. Managed laptops can block new USB keyboards by policy. If so, typing isn't available on that laptop: don't work around the policy or install drivers on it, and ask its IT owner. Reading still works. |
| `keyboard/mouse busy` | Another program is typing. Wait for it to finish. |
| `token` or `port` errors | Run `nanokvm stop`, or end stray `pythonw.exe` processes, then retry. If another program uses the port (8765), set `port` in `nanokvm.toml`. Logs are in `.run\service.log`. |
| Glimmer is slow (a full-screen read takes much longer than about 7 s) | Use the laptop's highest performance mode (on many gaming laptops it's set in the maker's own app, not by the Windows power plan), on its **full-power charger**, with no display on the NVIDIA GPU. A quiet power mode, battery and USB-C (PD) power held this laptop's GPU to about 55 W, and `run-glimmer.ps1` warns about each. Once, at full power on the charger, the GPU still stayed near 55 W for a reason not yet found. To see whether that is happening, run `nvidia-smi --query-gpu=power.draw,clocks.gr --format=csv -l 1` during a read. |
| VS Code or the desktop stalls while Glimmer runs | Check whether the NVIDIA GPU also drives a display: `nvidia-smi --query-gpu=display_active --format=csv` says *Enabled*. Then the desktop shares the GPU's 24 GB with Glimmer (21.7 GB with the drafter). Keep the displays off it: use the laptop's hybrid graphics mode, not a mode that wires the built-in screen to the NVIDIA GPU, and put any external monitor on a port the integrated GPU drives (on 29 Oct, in hybrid mode with the monitor on a DisplayLink USB-C dock, it said *Disabled*). Then Glimmer has the whole GPU. Until then, `run-glimmer.ps1 -NoDFlash` (20.9 GB) leaves the desktop more room, at the cost of slower long answers (describe 13.5 s instead of 6.7 s). |

## 12. Files and testing without hardware

Files marked **(new)** were written during the event. The others existed before it (`pre-event-baseline`),
and most gained event work too, which `git diff pre-event-baseline` shows. Folders marked *(not in the
repo)* are git-ignored and exist only on the AI computer.

```
E:\meta_hackathon
├── README.md                     this file
├── HACKATHON.md                  what was built before and during the hackathon (new)
├── LEARNINGS.md                  why it's built this way, and what I learned about the models (new)
├── LICENSE                       MIT licence (new)
├── docs\wiring.svg               wiring diagram used in this README
├── nanokvm.toml                  settings
├── pyproject.toml, uv.lock, uv.toml
├── src\nanokvm_mcp\
│   ├── glimmer.py                client for the local Muse Glimmer server (new)
│   ├── redact.py                 placeholders for secrets, restored only on this laptop (new)
│   ├── outbound.py               the outbound gate: the only path from screen text to Meta (new)
│   ├── spark.py                  client for Muse Spark (Meta Model API): text only, Standard models only (new)
│   ├── pipeline.py               read, ask, edit and fix: Glimmer, the gate and the coder together (new)
│   ├── editing.py                apply_edit: checks, the diff, and typing an approved edit into VS Code (new)
│   ├── viewer.py                 the `nanokvm viewer` window (Tk), with the prompt box (new)
│   ├── assist.py                 the prompt box's work: questions and Fix it, approvals in the window (new)
│   ├── acting.py                 Fix it: steps in any program, planned and found by Glimmer on this laptop (new)
│   ├── localcoder.py             Glimmer as the fix loop's coder, on this laptop (new)
│   ├── service.py                shared background service (capture, keyboard/mouse, the viewer's picture, HTTP API)
│   ├── client.py                 talks to / auto-starts the service
│   ├── cli.py                    the `nanokvm` command
│   ├── config.py                 settings: nanokvm.toml, overridden by NANOKVM_* environment variables
│   ├── capture.py                HDMI capture (OpenCV DirectShow; the KVM matched by USB ID only)
│   ├── hid.py, protocol.py, keys.py   keyboard/mouse over the CH9329-compatible serial protocol
│   ├── geometry.py               coordinate spaces and frame helpers
│   └── simulator.py              simulated locked computer (Notepad, or a small VS Code) for tests
├── tests\                        298 automated tests (simulator, mock Glimmer and Meta API: no hardware, GPU or key);
│                                 11 of its 17 files are new
├── tools\
│   ├── run-glimmer.ps1           starts llama-server with Glimmer on 127.0.0.1:8001
│   ├── glimmer_smoke.py          Glimmer smoke test on a rendered dummy C++ screen
│   ├── spark_smoke.py            Meta Model API smoke test with dummy text (key: .run\meta_api_key)
│   ├── glimmer_point.py          Fix it: does Glimmer point at the right thing? (a synthetic screen, dummy names) (new)
│   ├── ab_screens\live_check.cpp the dummy file for `nanokvm read --check` (new)
│   └── downloads\, llama.cpp\    llama.cpp b10976 (Windows CUDA 13.3) and its two zips (not in the repo)
├── models\, .cache\nv\           Glimmer weights and NVIDIA's kernel cache, section 5a (not in the repo)
├── .venv\  .uv\                  Python and packages, local to this folder (not in the repo)
└── .run\                         token, keys, audit log (created at runtime; not in the repo)
```

**On another Windows PC, with no KVM, GPU or key.** With [uv](https://docs.astral.sh/uv/) installed:

```powershell
git clone https://github.com/ramkumarkoppu/meta_hackathon
cd meta_hackathon
uv sync
uv run pytest -q                 # the tests below (about 3.5 minutes on this laptop)
$env:NANOKVM_SIMULATE = "1"; $env:NANOKVM_PORT = "8790"
uv run nanokvm viewer            # the live window, showing a simulated screen
uv run nanokvm stop
Remove-Item Env:NANOKVM_SIMULATE, Env:NANOKVM_PORT   # this terminal uses the real KVM again
```

Ask and Fix it in that window need Glimmer running
([section 5a](#5a-muse-glimmer-on-the-ai-computers-gpu-open-weights)). On the AI computer, use
`.venv\Scripts\nanokvm.exe` in place of `uv run nanokvm`, and `.venv\Scripts\python.exe -m pytest -q`
for the tests.

**The tests** cover the pipeline's logic against a simulated locked computer, a mock Glimmer server and
a mock Meta API. The mock Glimmer answers from the simulator's own text, so these tests don't measure
how well the real Glimmer reads a screen. The runs on the stand-in laptop did that. The tests check
that no image or `data:` URL reaches "Meta", that `edit` selects, deletes and types nothing until the
status bar and the lines on the screen check out (only Go to Line is pressed before that), that it
stops after a failed check, and that Ctrl+C releases every key while the service is still typing.

## 13. Licence

MIT: see [LICENSE](LICENSE). It covers everything in this repo, including the groundwork from before
the event. Muse Glimmer's weights (Apache-2.0), llama.cpp (MIT) and the Python packages aren't in the
repo: they're downloaded during the install ([section 5](#5-install)) and keep their own licences.

The keyboard/mouse code speaks WCH's CH9329 serial protocol: frames `57 AB 00 CMD LEN DATA SUM` at
57600 baud, the absolute mouse on a 0–4095 grid, and key codes from the USB HID Usage Tables. I checked
it against Sipeed's [NanoKVM-USB app](https://github.com/sipeed/NanoKVM-USB) (v1.1.4), which uses the
same protocol, but none of its code is copied here.
