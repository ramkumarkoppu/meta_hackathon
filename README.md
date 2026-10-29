# Locked Computer → Claude Code via NanoKVM-USB

This setup lets **Claude Code in VS Code on the AI computer** see the screen of the **locked computer**
live. When you allow it, Claude can also use the locked computer's keyboard and mouse. A Sipeed
**NanoKVM-USB** sits between the two laptops.

- **Two separate desks, no docking station.** Each laptop has its own charger and one Dell monitor.
  The Logitech keyboard and mouse switch between the laptops over Bluetooth (Easy-Switch).

- **Nothing is installed on the locked computer.** It only sends its picture out over HDMI and, if
  cable 4 is plugged in, sees one extra standard USB keyboard and mouse.
- **The locked computer stays offline.** Only the AI computer talks to the internet (to Claude).
- **Secure mode is on.** You approve **once** to share the locked computer screen with a Claude
  conversation, and **every** click or keypress needs its own approval
  ([section 2](#2-secure-mode-how-its-configured)).

**Hackathon entry.** This repo is my solo entry in the Meta Global AI Developer Hackathon (Indie
Developer track). The bridge described here existed before the event. What I built during it, from
26 Oct, is Meta models reading the locked computer's screen: Muse Glimmer on the AI computer's GPU,
and Muse Spark, which gets only redacted text. [HACKATHON.md](HACKATHON.md) says exactly what was
built before and during the event.

---

## Contents

1. [How it works](#1-how-it-works)
   - [1a. Operating systems](#1a-operating-systems)
2. [Secure mode (how it's configured)](#2-secure-mode-how-its-configured)
3. [Wiring for this desk](#3-wiring-for-this-desk)
   - [3a. Parts list](#3a-parts-list)
4. [Locked computer settings](#4-locked-computer-settings)
5. [Install (already done)](#5-install-already-done)
   - [5a. Local Muse Glimmer (hackathon tests)](#5a-local-muse-glimmer-hackathon-tests)
6. [Connect to Claude Code in VS Code](#6-connect-to-claude-code-in-vs-code)
7. [Using it](#7-using-it)
8. [Tools Claude gets](#8-tools-claude-gets)
9. [First run with the hardware](#9-first-run-with-the-hardware)
10. [Settings (nanokvm.toml)](#10-settings-nanokvmtoml)
11. [Safety details](#11-safety-details)
12. [Troubleshooting](#12-troubleshooting)
13. [Files and testing without hardware](#13-files-and-testing-without-hardware)

**Separate pages:** [What I built during the hackathon](HACKATHON.md) ·
[Hackathon plan: Meta models](docs/hackathon-plan.md)

---

## 1. How it works

![Wiring diagram: Locked computer → NanoKVM-USB → AI computer, with the five numbered cables](docs/wiring.svg)


| Part | Runs on | Job |
|------|---------|-----|
| NanoKVM-USB | hardware | Passes the locked computer's HDMI picture through to Monitor A (loop-out) and, as a USB capture device, to the AI computer (up to 1920x1080). With cable 4, it also acts as a USB keyboard and mouse for the locked computer. |
| `nanokvm` service | AI computer, in the background | The **only** program that opens the NanoKVM. It reads the video continuously (~30 fps), sends keyboard/mouse commands, and serves a live view on `127.0.0.1`. It starts automatically and stops after 60 idle minutes (an open live view keeps it running). |
| `locked-computer` MCP server | AI computer, one per Claude Code session | Gives Claude its tools (`screenshot`, `zoom`, `wait_for_change`, `click`, `type_text` and so on). |

**What "realtime" means here.** Claude doesn't watch continuous video; it looks at an image each
time a tool returns one. This setup gets as close as Claude Code allows:

- **Always current.** Every screenshot is the current frame (~0.1 s old).
- **Reacting to changes.** `wait_for_change` makes Claude respond as soon as the screen changes.
- **Live view for you.** You can watch the locked computer screen live in a window on the AI
  computer (`nanokvm viewer`) or in a VS Code tab.

### 1a. Operating systems

The two computers don't need the same operating system. To the locked computer, the NanoKVM is just
a monitor and a USB keyboard and mouse. To the AI computer, it's a USB video capture device (with an
audio device beside it for any HDMI sound, which the bridge never opens) and a USB serial port.

| Computer | Windows | macOS | Linux |
|----------|---------|-------|-------|
| Locked computer | Picture tested (Windows 11); keyboard and mouse not yet | Should work; not tested | Should work; not tested |
| AI computer | Tested (Windows 11) | Not yet: needs porting | Not yet: needs porting |

**A locked computer on macOS or Linux.** Set `locked_os = "macos"` or `"linux"` in `nanokvm.toml`
([section 10](#10-settings-nanokvmtoml)). Claude is then told which shortcuts to use: on a Mac,
`cmd` where Windows uses `ctrl`. Also:

- `keyboard_layout` must match the layout chosen on that computer. `"uk"` and `"us"` are PC
  layouts, and Linux's English (UK) matches `"uk"`. A Mac's British layout puts some characters
  elsewhere, so on a Mac add the U.S. input source and set `"us"`.
- A Mac may open *Keyboard Setup Assistant* the first time it sees the NanoKVM's keyboard. If it
  asks for the keyboard type, choose ANSI, which matches `"us"`.
- [Section 4](#4-locked-computer-settings) uses Windows names, and its rule that the NanoKVM's mouse
  targets the main display is how Windows behaves. On macOS 13 or later, open *System Settings >
  Displays*, select Monitor A and set *Use as* to *Main display*; on macOS 12 or earlier, drag the
  white menu bar onto Monitor A in *System Preferences > Displays > Arrangement*. This is untested,
  so if clicks miss, make Monitor A the only display. On Linux the NanoKVM's mouse may span every
  display that is on (it does on X11), so keep Monitor A the only display (GNOME: *Settings >
  Displays*).
- Section 7's VS Code shortcuts are the Windows and Linux ones. On a Mac they use Cmd (Cmd+Shift+P,
  Cmd+B, Cmd+J, Cmd+P, Cmd+F, Cmd+Shift+O), except Go to Line, which stays Ctrl+G.

**The AI computer must run Windows for now:** capture uses DirectShow, so on macOS or Linux it stops
with *video capture needs Windows on the AI computer*. Running it elsewhere is future work
([hackathon plan, section 8](docs/hackathon-plan.md#8-open-questions-and-risks)).

## 2. Secure mode (how it's configured)

The locked computer is used for isolated, mostly offline, secure work. The setup gives you AI help
while keeping you in control:

| Protection | How |
|------------|-----|
| Claude acts only when you allow it | There are **two gates**. **Physical:** the keyboard/mouse only work while cable 4 is plugged in; unplug it and the setup is view-only. **Per action:** Claude Code asks you before **every** click or keypress, even in auto mode (`confirm_actions = true`). |
| One approval to share the screen | Claude's screen tools work only after Claude calls `start_screen_sharing`, which **always** asks you, even in auto mode. Once you approve, that conversation can take screenshots, zoom and wait for changes without further prompts. Sharing lasts as long as Claude asks for in that call: **60 minutes** by default (`share_minutes`), at most 480, so check the number in the prompt. It ends sooner if you say stop and Claude calls `stop_screen_sharing`, or when the service stops. When sharing is off, the service refuses captures requested by Claude's tools. The `nanokvm` command and the live view aren't gated yet ([section 8](#8-tools-claude-gets)). |
| Claude sees only one screen | Only Monitor A (the screen wired through the NanoKVM) is captured. The locked computer's built-in screen and everything on the AI computer's desk, including Monitor B, are never seen. |
| Nothing else goes *into* the locked computer | No network, no software, no drivers. HDMI carries the picture *out*; the only thing that goes back is the standard monitor identification (EDID) every monitor sends. Cable 4 carries only keyboard and mouse input. |
| Physical off switch | Unplug cable 3 (NanoKVM → AI computer): capture and control are then impossible. Monitor A goes dark too (see [section 3](#3-wiring-for-this-desk)). |

**What still leaves:** every image Claude gets is sent to Anthropic as part of the conversation.
While sharing is on, that means screenshots, zooms, `wait_for_change` results and the screenshot
after each action. Claude can also open the screenshots that the `nanokvm` command saves in `.run\`
at any time, so delete them when you're done ([section 8](#8-tools-claude-gets)). Keep anything you
may not share off Monitor A while sharing is on. Use this setup only with the approval of whoever
owns the locked computer and its data, for example your employer or client.

## 3. Wiring for this desk

![Wiring diagram: Locked computer → NanoKVM-USB → AI computer, with the five numbered cables](docs/wiring.svg)

The diagram is `docs\wiring.svg`. Press **Ctrl+Shift+V** in VS Code to see it here.

**Know the NanoKVM-USB's ports.** Your unit is a small black box with **"TARGET"** printed at one
end of the top and **"HOST"**, with a laptop icon, at the other. **Each end has one full-size HDMI
port and one USB-C port.** The icons on the side show that the picture flows from the TARGET end
to the HOST end:

```
                 top:  [ TARGET ]             NanoKVM USB              [ 💻 HOST ]
                ┌─────────────────────────────────────────────────────────────────┐
  TARGET end ───┤ HDMI  (icon: monitor →)  = video IN   ◄── cable 1               │
                │ USB-C (icon: keyboard+mouse) = to locked computer ── cable 4    │
                │                                                                 ├─── HOST end
                │      HDMI  (icon: monitor →) = video OUT (loop-out) ─────────────► cable 2
                │      USB-C (icon: monitor+keyboard+mouse) = to AI computer ──────► cable 3
                └────────────── side: USB-A port + [ T | H ] switch ──────────────┘
```

| End | Port | Side icon | Cable | Goes to |
|-----|------|-----------|-------|---------|
| **TARGET** | HDMI | monitor → | **1** (HDMI) | Locked computer's HDMI port. This is the video **input**. |
| **TARGET** | USB-C | keyboard + mouse | **4** (USB-C → USB-A) | Locked computer's USB-A port, so it sees a keyboard and mouse |
| **HOST** | HDMI | monitor → | **2** (HDMI → DVI-D) | Dell Monitor A's white DVI port. This is the video **output** (loop-out). |
| **HOST** | USB-C | monitor + keyboard + mouse | **3** (USB-C → USB-C) | AI computer. It carries the video and keyboard/mouse data, and powers the NanoKVM. |
| side | USB-A + T/H switch | – | none | Not used. Leave the switch at **H** (as shipped): a USB stick in this port would then only reach the AI computer, never the locked computer. |

> ⚠️ The most likely mistake is plugging the locked computer's HDMI into the **HOST** end. That port
> is an output, so you'd get no picture anywhere. The locked computer always connects to the
> **TARGET** end.

**The complete wiring (two desks, no docking station):**

```
LOCKED COMPUTER DESK
[1] locked computer HDMI port ── HDMI ────────────► NanoKVM TARGET HDMI
[4] locked computer USB-A port ─ USB-A → USB-C ───► NanoKVM TARGET USB-C   (only when Claude may act)
[2] NanoKVM HOST HDMI ────────── HDMI → DVI-D ────► Dell Monitor A, white DVI port

AI COMPUTER DESK
[3] NanoKVM HOST USB-C ───────── USB-C → USB-C ───► AI computer USB-C
[5] AI computer HDMI port ────── HDMI → DVI-D ────► Dell Monitor B, white DVI port

Logitech keyboard + mouse: Bluetooth to both laptops; Easy-Switch between them.
Each laptop on its own charger. No docking station.
```

Setup steps:

1. **Disconnect the docking station** from the locked computer and both monitors, and plug each
   laptop into its own charger.
2. Connect cables **1, 2, 3 and 5**. Connect cable **4** only when Claude should be able to act.
3. On **both monitors**, use the buttons to set the **input source to DVI**.
4. On the locked computer, make **Monitor A the main display** ([section 4](#4-locked-computer-settings)).

Notes:

- **Why DVI.** The Dell monitors have DisplayPort, DVI-D and VGA inputs, but no HDMI. HDMI and
  DVI-D carry the same digital picture, so a passive HDMI-to-DVI-D cable needs no power or
  converter. Don't use VGA: it's analogue and blurry.
- **Monitor A depends on the AI computer.** The NanoKVM is powered through cable 3. If the
  AI computer is off or cable 3 is unplugged, Monitor A goes dark. To use the locked computer
  alone, unplug cable 1 and plug cable 2's HDMI end straight into the locked computer's HDMI port.
- **Why cable 4 is USB-A at the laptop end.** Some users found that USB-C to USB-C on the
  NanoKVM's TARGET side doesn't work.
- **Keyboard and mouse.** Use the Logitech **Easy-Switch** buttons to change laptops. Logitech
  *Flow* would need Logitech software and a network connection on the locked computer, so don't use
  it.
- **The NanoKVM's side USB-A port and T/H switch** are for a USB stick and aren't needed. Leave
  the switch at **H**, so nothing plugged in there can reach the locked computer. The switch doesn't
  affect video, capture or keyboard/mouse.

### 3a. Parts list

The NanoKVM-USB came with **no cables**. Ordered on Amazon UK on 2026-10-07:

| # | Ordered | Connects |
|---|---------|----------|
| 1 | Ubluker HDMI cable (48 Gbps), 1 m | Locked computer HDMI → NanoKVM TARGET HDMI |
| 2 | [C2G HDMI to DVI-D cable](https://www.amazon.co.uk/C2G-Digital-Suitable-Raspberry-Stations/dp/B00CQSYS48), 1 m | NanoKVM HOST HDMI → Dell Monitor A DVI |
| 3 | [Cable Matters USB-IF certified USB-C to USB-C, 10 Gbps](https://www.amazon.co.uk/USB-IF-Certified-Cable-Matters-Delivery/dp/B01L0F6AJI), 1 m | NanoKVM HOST USB-C → AI computer. Must be a USB 3 data cable: it carries the video and powers the NanoKVM. |
| 4 | [UGREEN USB-A to USB-C, USB 3.0](https://www.amazon.co.uk/dp/B08XJP9GMP), 1 m | Locked computer USB-A → NanoKVM TARGET USB-C (keyboard/mouse) |
| 5 | C2G HDMI to DVI-D cable, 2 m | AI computer HDMI → Dell Monitor B DVI |

## 4. Locked computer settings

**Display** (*Settings > System > Display* on Windows 11; for macOS and Linux see
[section 1a](#1a-operating-systems)):

- Use **Extend** mode: the laptop's own screen + Monitor A.
- **Monitor A must be the main display.** Select Monitor A and tick *Make this my main display*.
  The NanoKVM's mouse always targets the main display, so this is what makes Claude's clicks
  land on the screen it sees.
- Set Monitor A to its **native resolution at 60 Hz**. The NanoKVM accepts up to 4K at 30 Hz and
  delivers 1920x1080 to the AI computer, so a 1920x1080 monitor gives Claude the sharpest picture.
- If Monitor A is **1920x1200** (16:10), Claude's screenshots look squashed. Add
  `display_aspect = "16:10"` to `nanokvm.toml`.

**Keyboard layout.** `type_text` types for the layout in `nanokvm.toml`, currently
`keyboard_layout = "uk"`. It must match the locked computer's keyboard layout, assumed to be English
(UK) but not yet checked: see the input indicator on its taskbar, or *Settings > Time & language >
Language & region*.
If that laptop uses US, set `"us"`. Shortcuts like `ctrl+c` work with either. If the locked
computer isn't Windows, set `locked_os` too ([section 1a](#1a-operating-systems)).

**Sleep.** A sleeping laptop sends no picture. When cable 4 is connected, Claude can wake the
screen with a keypress if you approve it.

## 5. Install (already done)

Everything lives in `E:\meta_hackathon`. Nothing was installed globally, the registry was not
touched, and the existing conda environments were not used.

| What | Location |
|------|----------|
| Python 3.12 (uv-managed) | `.uv\python\cpython-3.12.14-windows-x86_64-none\` |
| uv download/build cache | `.uv\cache\` (set in `uv.toml`) |
| Virtual environment and packages (`mcp`, `opencv-python-headless`, `pyserial`, …) | `.venv\` |
| This project's code / tests | `src\nanokvm_mcp\` / `tests\` |
| Claude Code MCP registration (this folder only) | `.mcp.json` |
| Claude Code permissions for this folder | `.claude\settings.local.json` |
| Settings | `nanokvm.toml` |
| Runtime files (token, keys, logs, screenshots, calibration) | `.run\` |

**The serial driver.** The NanoKVM's keyboard/mouse channel is a WCH CH343 USB-serial chip
(`1A86:55D3`). When it was first plugged in (22 Oct), Windows used the WCH driver already on this
laptop, so nothing was installed for it. Like any driver, it lives in Windows, not in this folder.
Video works without it.

To rebuild the environment from scratch, run this in PowerShell in this folder. It uses `uv`, which
isn't in this folder: it's the copy already installed in `%USERPROFILE%\.local\bin` (uv 0.12.5
built the current `.venv`).

```powershell
$env:UV_PYTHON_INSTALL_DIR = "E:\meta_hackathon\.uv\python"
uv python install 3.12.14 --no-bin --no-registry
uv venv .venv --python "E:\meta_hackathon\.uv\python\cpython-3.12.14-windows-x86_64-none\python.exe"
uv sync
```

`E:` is formatted exFAT, which has no symlinks or hardlinks. That's why `uv.toml` sets
`link-mode = "copy"`. During `uv python install`, the message *"Failed to create Python minor
version link directory"* is harmless.

### 5a. Local Muse Glimmer (hackathon tests)

For the hackathon tests, Meta's open-weights **Muse Glimmer** runs on the AI computer's GPU
through llama.cpp. The bridge doesn't use it yet. The launcher's options and the reasons behind the
setup are in [the hackathon plan, section 6](docs/hackathon-plan.md#6-install-locations), and the
measurements in [section 7](docs/hackathon-plan.md#7-schedule-and-status).

| What | Location |
|------|----------|
| llama.cpp b10976 (Windows CUDA 13.3 build) | `tools\llama.cpp\` |
| Glimmer weights: Meta's, and Unsloth's (the default) | `models\`, `models\unsloth\` |
| Launcher and test scripts | `tools\` ([section 13](#13-files-and-testing-without-hardware)) |
| NVIDIA's compiled-kernel cache | `.cache\nv\` |
| Key for the local model server (made on first start) | `.run\llama_api_key` |

Start the server, then check it from a second terminal:

```powershell
# Terminal 1: llama-server on http://127.0.0.1:8001 only; Ctrl+C stops it
E:\meta_hackathon\tools\run-glimmer.ps1
# Terminal 2: waits for the server, has Glimmer read a dummy C++ screen, prints accuracy, speed and GPU memory
E:\meta_hackathon\.venv\Scripts\python.exe E:\meta_hackathon\tools\glimmer_smoke.py
```

Glimmer is fast only in Turbo mode on the 240 W barrel charger; see *Glimmer is slow* in
[section 12](#12-troubleshooting).

## 6. Connect to Claude Code in VS Code

The server is registered **for this folder** in `.mcp.json`, and is already connected and approved.

1. In VS Code, open the folder **`E:\meta_hackathon`**.
2. Start a **new** Claude Code conversation. After changing `nanokvm.toml`, run `nanokvm stop` first
   ([section 10](#10-settings-nanokvmtoml)).
3. Ask: *"Take a screenshot of my locked computer."* Type `/mcp` to see the server's status.

## 7. Using it

### Watching the locked computer screen yourself

Run `E:\meta_hackathon\.venv\Scripts\nanokvm.exe viewer`. A window on the AI computer shows the
locked computer's screen live, at up to 30 fps (`--fps` changes it). Two status lines under the
picture show:

- the capture mode, the capture and window frame rates, and the delay from capture to the window;
- a warning if the picture is uniform (no HDMI signal, a sleeping display or a blank screen);
- the keyboard/mouse COM port and, once Claude has used it, whether the NanoKVM reports cable 4 as
  connected;
- whether any Claude conversation is sharing the screen.

The window is read-only: keys and clicks in it stay on the AI computer, it never opens the
keyboard/mouse link, and it saves nothing to disk. Claude can't see it, but it's an ordinary
window, so it does show if you share the AI computer's screen in a call.

Keys: **F11** or a double-click for full screen (**Esc** leaves it), **1** to switch between fitting
the window and actual pixels (drag to pan), **R** to start the service if it has stopped, **Q** to
close. When it closes, it prints how many frames it showed and the delay.

### Watching it inside VS Code instead

1. Run `E:\meta_hackathon\.venv\Scripts\nanokvm.exe viewer --no-open` in the terminal.
2. Copy the printed URL (`http://127.0.0.1:8765/?t=…`).
3. Press **Ctrl+Shift+P**, choose **Simple Browser: Show**, and paste the URL.

The view is live (~15 fps) and read-only. Claude's `screen_status` tool also shows this URL. Treat
the URL like a password: its token also unlocks the service's keyboard and mouse controls.

### Asking Claude

The first time Claude needs to look, it asks to **share the screen**. Approve once, and it can
then look as often as it needs during that sharing session. Say "stop sharing" when you're done:

- *"Look at my locked computer and explain the error dialog."* Claude uses `screenshot`, then `zoom`
  for small text.
- *"Wait until the build on the locked computer finishes, then summarise it."* Claude uses
  `wait_for_change`.
- *"Open Notepad on the locked computer and type this draft."* This needs cable 4. Claude uses
  `press_keys` and `type_text`, and you approve each step.

### Reading C++ code in VS Code on the locked computer

Code is small, thin, coloured text, which is the hardest thing for a video capture. These
settings make it easy to read, both for you in the live view and for Claude.

**On the locked computer.** These are only VS Code settings; nothing is installed. Open
*Ctrl+Shift+P > Preferences: Open User Settings (JSON)* and add:

```jsonc
"editor.fontSize": 16,                // default 14; 16 stays legible in Claude's screenshots
"editor.minimap.enabled": false,      // more width for code
"editor.wordWrap": "on",              // no code hidden off the right edge
"editor.stickyScroll.enabled": true   // the current class/function name stays visible at the top
```

- **Maximise VS Code on Monitor A,** running at **1920x1080**. While Claude reads, hide the side
  bar (**Ctrl+B**) and the panel (**Ctrl+J**). That gives about 40–45 lines per screen.
- **Keep line numbers on** (the default), so you and Claude can refer to exact lines.
- **Theme is optional.** A light theme (*Light Modern*) survives video compression best. Dark
  themes work, but thin coloured text is slightly softer.

**How Claude reads code:**

- **One screen:** Claude takes a `screenshot`, and uses `zoom` on any lines that are hard to
  read. Zoom shows them at full HDMI resolution, enlarged.
- **A whole file, you scrolling:** press **Page Down**, wait until Claude has read that screen and
  called `wait_for_change` again, then repeat. `wait_for_change` only notices changes made after
  Claude calls it, so a page you skip past while Claude is still reading is never seen. Scrolling
  fast with the mouse wheel doesn't work, because Claude only sees the screen when it settles.
- **A whole file, Claude navigating (cable 4):** Claude uses VS Code's keyboard shortcuts, and
  you approve each keypress:

  | Shortcut | Action |
  |----------|--------|
  | **Ctrl+P** | open a file by name |
  | **Ctrl+G** | go to a line |
  | **Ctrl+F** | find |
  | **Ctrl+Shift+O** | go to a function or class |
  | **F12** | go to the definition |
  | **Page Down** | next screen |

Example requests:

- *"Explain the function on my locked computer screen."*
- *"I'll page through decoder.cpp; review it for bugs as I go."*
- *"Open decoder.cpp with Ctrl+P, go to parse_frames and explain it."*

**If Claude will type C++ into VS Code (cable 4).** VS Code's typing helpers would garble code
typed key by key: they double closing brackets and quotes, add extra indentation, and let Enter
accept auto-complete suggestions. While Claude types, switch them off in the same settings file:

```jsonc
"editor.autoClosingBrackets": "never",
"editor.autoClosingQuotes": "never",
"editor.autoIndent": "none",
"editor.formatOnType": false,
"editor.acceptSuggestionOnEnter": "off"
```

Typing runs at about 20 characters a second, so ask Claude for small, targeted edits rather than
whole files.

### Using your own Bluetooth keyboard and mouse at the same time

They keep working normally. Switch between the laptops with the Logitech Easy-Switch buttons.
With cable 4 in, the locked computer simply sees one more keyboard and mouse. While your keyboard and
mouse are switched to the AI computer (for example, while you talk to Claude), Claude's
actions on the locked computer can't clash with yours.

- **Don't touch the mouse while Claude clicks.** You share one pointer, so moving yours can make
  Claude's click land elsewhere.
- **Don't hold Shift, Ctrl or Alt while Claude types.** Windows shares modifier keys across
  keyboards.
- **Stop Claude at any time.** Press **Esc** in Claude Code, which stops the action and releases
  all keys, or unplug cable 4.
- **Caps Lock is shared.** Typing compensates for it when the NanoKVM reports the Caps Lock state
  (to be confirmed on the hardware). If letters come out in the wrong case, switch Caps Lock off.
- **Your keystrokes stay private.** The NanoKVM can only send keystrokes, not read them, so neither
  it nor Claude sees what you press. While sharing is on, Claude can see whatever your typing puts
  on Monitor A.

## 8. Tools Claude gets

| Tool | What it does | Permission (this setup) |
|------|--------------|-------------------------|
| `start_screen_sharing` | Shares the locked computer screen with this conversation for the minutes Claude asks for (60 by default, at most 480) | **asks you, always** |
| `stop_screen_sharing` | Stops sharing | allowed |
| `screenshot` | Live screenshot, 1456x819 for a 16:9 picture (smaller at other shapes, for example 1370x856 at 16:10). Its pixels are the coordinate space for pointer tools. | allowed **while sharing** |
| `zoom` | Sharp, enlarged view of an area at full HDMI resolution, for small text and code | allowed **while sharing** |
| `wait_for_change` | Waits until the screen changes and settles, then returns it | allowed **while sharing** |
| `screen_status` | Video signal and fps; keyboard/mouse link; locked computer USB connected; Caps Lock; live-view URL. No screen content, but the URL contains the service's access token, so the token goes to Anthropic with the conversation. | allowed |
| `click`, `move_mouse`, `drag`, `scroll` | Mouse actions. Each returns a screenshot after the screen settles. | asks you, every time |
| `type_text` | Types text for the configured layout (UK), ~20 characters/s, up to 1500 per call | asks you, every time |
| `press_keys` | Shortcuts and keys: `ctrl+s`, `win+r`, `alt+tab`, `ctrl+alt+delete`, `down down enter` | asks you, every time |
| `release_all` | Releases every key and button and stops typing in progress | asks you, every time |
| `calibrate` | Measures where clicks land; only needed if clicks are off | asks you, every time |
| `stop_service` | Stops the service and frees the NanoKVM | asks you (an ordinary prompt) |

"Allowed" tools are pre-approved in `.claude\settings.local.json`. "While sharing" is checked by
the service, but only for Claude's tools, which send an ID made when the `locked-computer` MCP server
starts (once per Claude Code session). Without an approved share for that ID, their captures are
refused; actions still work but return no screenshot. If a session continues into a new
conversation (for example with `/clear`; Claude Code's docs don't say whether that restarts the
server), the share may carry over, so ask Claude to stop sharing before you clear. Requests
without an ID, from the `nanokvm` command (`screenshot`, `watch`, `diag`) and the live view, are not
gated: they need only the token in `.run\token`. Any program on the AI computer that can read
the token can make them, including a shell command run by an AI.

"Asks you, always" and "asks you, every time" are Claude Code permission prompts, shown even in auto
mode because those tools are marked `anthropic/requiresUserInteraction`; for the keyboard/mouse
tools, that comes from `confirm_actions = true`. Plain "asks you" is Claude Code's ordinary prompt,
which auto mode may skip. These prompts don't apply to other AI tools or to programs that call the
service directly: the service accepts any request that carries the token, including one that turns
sharing on. With `control = false`, the keyboard/mouse tools disappear and the service refuses
keyboard/mouse requests from any program.

**Until this is fixed,** use Claude Code's default permission mode (not auto mode) in this folder
while the locked computer shows anything you may not share, so shell commands ask you first, and set
`control = false` when Claude doesn't need to act. The fix, approvals owned by the service, is
change 1 in [docs/hackathon-plan.md](docs/hackathon-plan.md#3-planned-code-changes). It isn't built
yet.

## 9. First run with the hardware

Wire everything as in [section 3](#3-wiring-for-this-desk), then run:

```powershell
E:\meta_hackathon\.venv\Scripts\nanokvm.exe diag              # devices, video, test screenshot
E:\meta_hackathon\.venv\Scripts\nanokvm.exe diag --mouse-test # also moves the locked computer pointer in a small square (needs cable 4)
```

`diag` checks:

- **Capture device.** It finds the NanoKVM's capture device by its USB ID (`345F:2131` or
  `345F:2133`). Your webcam is listed but never opened.
- **COM port.** It finds the keyboard/mouse port by the USB IDs in `serial_ids` (this unit: CH343,
  `1A86:55D3`).
- **Video.** It starts the service and saves a test screenshot to `.run\diag.jpg`.
- **Keyboard/mouse (cable 4).** Plain `diag` doesn't open this link, so it doesn't test cable 4. Run
  `diag --mouse-test` and check that the locked computer's pointer moves. After that, `nanokvm status`
  shows `'target_usb_connected': True` if the NanoKVM reports cable 4 as connected.

It ends with `RESULT: OK` or a list of problems with fixes. Then run `nanokvm viewer` and check the
picture by eye ([section 7](#watching-the-locked-computer-screen-yourself)).

Other commands (prefix with `E:\meta_hackathon\.venv\Scripts\`):

```powershell
nanokvm status          # service, video and keyboard/mouse state (--json for the raw data)
nanokvm screenshot      # saves .run\screenshot.jpg, or the file you name
nanokvm viewer          # live view in a window (--browser: in your browser; --no-open: only prints the URL)
nanokvm watch           # prints a line per screen change and saves .run\watch\latest.jpg
nanokvm start | stop    # the service also starts automatically when Claude uses a tool
nanokvm serve           # run the service in the foreground with console logging (debugging)
```

`watch` options: `--min-change` (percent of the screen, default 0.5), `--settle` (milliseconds the
screen must stay still, default 700), `--keep` (also saves every changed frame in `.run\watch\`)
and `--count N` (stops after N changes). Like `nanokvm screenshot`, it isn't gated by screen
sharing ([section 8](#8-tools-claude-gets)).

Every screenshot the `nanokvm` command saves (`screenshot`, `watch`, `diag`, including a file you
name) is **deleted automatically after 5 minutes** (`saved_image_minutes`). The service deletes
them every 15 seconds and deletes all of them when it stops; each `nanokvm` command also cleans up
when it starts. Deleting doesn't erase the data from the SSD, but it stops screenshots of the Work
laptop piling up where other programs and AI file tools can read them.

## 10. Settings (`nanokvm.toml`)

| Setting | This setup | Effect |
|---------|------------|--------|
| `control` | `true` | Keyboard/mouse tools on. `false` = view-only. |
| `confirm_actions` | `true` | Claude Code asks before every keyboard/mouse action, even in auto mode. The default is `false`, so keep `confirm_actions = true` in `nanokvm.toml`. |
| `keyboard_layout` | `"uk"` | The locked computer's keyboard layout, for `type_text`: `"uk"` or `"us"`. These are PC layouts; on a Mac use `"us"` ([section 1a](#1a-operating-systems)). |
| `locked_os` | `"windows"` (default) | The locked computer's OS: `"windows"`, `"macos"` or `"linux"`. Claude is told it, so it uses that system's shortcuts. With any other value Claude is told the OS is unknown and asks you, and `nanokvm diag` reports it. |
| `display_aspect` | not set | `"16:10"` if Monitor A is 1920x1200 and the picture looks squashed |
| `shot_width` | 1456 | Screenshot width Claude sees. 1456 keeps code legible; it's capped automatically so Claude never gets a downscaled image. |
| `viewer_fps` / `viewer_width` / `viewer_quality` | 15 / 1920 / 90 | The browser live view's frame rate, and the sharpness and quality of both live views. Use `viewer_fps = 30` for a smoother browser view (more CPU); the `nanokvm viewer` window asks for 30 fps itself (`--fps`). |
| `capture_fourcc` | `"MJPG"` | Try `"YUY2"` (uncompressed, sharpest code text) on hardware day, if `nanokvm diag` lists the NanoKVM as *USB3 Video*. |
| `serial_port` | auto | Force a COM port, for example `"COM5"` |
| `serial_ids` | `"1A86:7523,1A86:55D3"` | USB IDs that identify the NanoKVM's serial port; this unit's chip is a CH343 (`1A86:55D3`) |
| `share_minutes` | 60 | Default length of a screen-sharing session; Claude can ask for 1–480 minutes when it starts one |
| `saved_image_minutes` | 5 | Screenshots saved by the `nanokvm` command are deleted after this many minutes (minimum 1) |
| `idle_exit_min` | 60 | Stop the background service after this many idle minutes (0 = never) |
| `port` | 8765 | Port of the service and the live view, always on `127.0.0.1`. Change it if another program uses 8765, but run `nanokvm stop` before you edit it. |
| `capture_width` / `capture_height` / `capture_fps` | 1920 / 1080 / 30 | Video mode requested from the NanoKVM |
| `region` | not set | Manual active area `"x,y,w,h"` in capture pixels, used instead of the `calibrate` result. Normally leave it unset. |
| `jpeg_quality` | 80 | JPEG quality of Claude's screenshots (`zoom` uses at least 90) |
| `key_hold_ms` / `key_gap_ms` / `click_hold_ms` | 25 / 15 / 60 | Keyboard and click timing in milliseconds. Raise them if the locked computer misses keys; typing gets slower. |
| `post_action_ms` | 1500 | Longest wait, in milliseconds, for the screen to react after an action before its screenshot |

Any setting can also come from an environment variable `NANOKVM_<NAME>`, for example
`NANOKVM_PORT`, which overrides `nanokvm.toml`.

After editing, run `nanokvm stop` (it restarts on the next tool call) and start a new Claude
conversation.

## 11. Safety details

- **No stuck keys.** Every action ends with all keys and buttons up, even when something fails.
  Releasing never moves the pointer.
- **One action at a time.** If two Claude conversations try to act at once, the second gets
  "busy". It's never queued to run later into whatever window has focus by then.
- **Clicks stay accurate.** After a calibration or display change, clicks are refused until Claude
  takes a new screenshot.
- **Only the NanoKVM's COM port is used.** It's auto-picked only when the NanoKVM itself is
  attached and exactly one port matches `serial_ids` (this unit: CH343, `1A86:55D3`). If several
  match (Arduinos often use the same chips), it picks none and asks you to set `serial_port`.
- **Screen text is untrusted.** Claude is told never to follow instructions that appear on the
  locked computer screen, never to type passwords unless you ask in the conversation, and to confirm before
  destructive actions.
- **The local service is reachable only from this laptop.** It listens only on `127.0.0.1`, rejects
  foreign Host headers, and requires the random token in `.run\token`, so web pages can't read the
  screen or type. It isn't locked down against programs on this laptop yet: any that can read the
  token can use the service without Claude Code's prompts ([section 8](#8-tools-claude-gets)). The
  token never changes by itself, so one that went out in a `screen_status` result stays valid. To
  replace it, run `nanokvm stop` and delete `.run\token`; the next start makes a new one.

## 12. Troubleshooting

| Symptom | Fix |
|---------|-----|
| No signal / black picture (`screen_status` says *blank* or *no_frames*) | Check that the locked computer HDMI goes into the **TARGET** end. Wake the locked computer. Check Monitor A is enabled in the locked computer's display settings ([section 4](#4-locked-computer-settings)). |
| Monitor A is black but capture works, or the other way round | Set Monitor A's input to **DVI**. Check Monitor A runs at its native resolution at 60 Hz ([section 4](#4-locked-computer-settings)). |
| `NanoKVM capture device not found` | Check cable 3, then run `nanokvm diag`. If the NanoKVM gets no power over USB-C to USB-C, use USB-A to USB-C into the AI computer. |
| `video capture needs Windows on the AI computer` | The bridge runs only on a Windows AI computer, for now. This limit doesn't apply to the locked computer ([section 1a](#1a-operating-systems)). |
| `COM port not found` (video works) | Run `nanokvm diag`; it lists every COM port. This unit's chip is a CH343 (`1A86:55D3`, shown as *USB-Enhanced-SERIAL CH343*, COM5 so far) and needs no extra driver. Check that `serial_ids` in `nanokvm.toml` still includes `1A86:55D3` (the built-in default looks only for `1A86:7523`), then reseat cable 3 or try another USB-C port: video and the serial chip use different wires in the cable. Also look for a disabled or yellow-flagged entry under *Ports (COM & LPT)* in Device Manager. |
| `COMx is in use` or `no video` | Close the official NanoKVM-USB app or the `usbkvm.sipeed.com` browser tab; only one program can hold the device. Use `nanokvm stop` before opening the official app. |
| Keyboard/mouse do nothing; `screen_status` says *locked computer USB NOT connected*, or `nanokvm status` shows `'target_usb_connected': False` | Check cable 4 and try the locked computer's other USB-A port. Managed laptops can block new USB keyboards by policy. If so, keyboard/mouse control isn't available on that laptop: don't work around the policy or install drivers on it; ask its IT owner. Viewing still works. |
| Clicks land on the wrong screen | Make Monitor A the **main display** ([section 4](#4-locked-computer-settings)). If clicks are offset on the right screen, ask Claude to run `calibrate`. |
| Wrong characters typed (for example `"` and `@` swapped) | `keyboard_layout` doesn't match the locked computer's keyboard layout (on a Mac, see [section 1a](#1a-operating-systems)). Fix it, run `nanokvm stop`, and start a new conversation. |
| `keyboard/mouse busy` | Another conversation, or a long `type_text`, is using it. Wait, or call `release_all`. |
| `coordinate space changed … take a new screenshot` | Expected after `calibrate` or a resolution change. |
| Screenshot text is blurry | Run Monitor A at 1920x1080. Ask Claude to use `zoom`. |
| `token` or `port` errors | Run `nanokvm stop`, or end stray `pythonw.exe` processes, then retry. If another program uses the port (8765), set `port` in `nanokvm.toml`. Logs are in `.run\service.log`. |
| Claude says the `locked-computer` tools don't exist | Open `E:\meta_hackathon` in VS Code and start a new conversation. Check `/mcp`. If it shows the server failed, run `.venv\Scripts\python.exe -m nanokvm_mcp.mcp_server` in a terminal to see the error (Ctrl+C quits); a bad value in `nanokvm.toml` is the usual cause. |
| Glimmer is slow (a full-screen read takes much longer than about 7 s) | Use Armoury Crate's **Turbo** mode (**Fn+F5** switches modes; changing the Windows power plan doesn't), on the **240 W barrel charger**, and GPU Mode **Standard** or **Optimized**. Silent mode, battery and USB-C (PD) power hold the GPU to about 55 W; `run-glimmer.ps1` warns about each. Once, in Turbo on the charger, the GPU still stayed near 55 W for a reason not yet found. To see whether that is happening, run `nvidia-smi --query-gpu=power.draw,clocks.gr --format=csv -l 1` during a read. Details: [hackathon plan, section 8](docs/hackathon-plan.md#8-open-questions-and-risks). |

## 13. Files and testing without hardware

```
E:\meta_hackathon
├── README.md                     this file
├── docs\
│   ├── wiring.svg                wiring diagram used in this README
│   └── hackathon-plan.md         hackathon plan: adding Meta models (not built yet)
├── nanokvm.toml                  settings (secure mode, UK layout)
├── .mcp.json                     registers the "locked-computer" MCP server with Claude Code
├── .claude\settings.local.json   Claude Code permissions: the "allowed" tools in section 8, plus web look-ups from the hackathon research
├── pyproject.toml, uv.lock, uv.toml
├── src\nanokvm_mcp\
│   ├── mcp_server.py             MCP tools for Claude
│   ├── service.py                shared background service (capture, keyboard/mouse, live view, HTTP API)
│   ├── client.py                 talks to / auto-starts the service
│   ├── viewer.py                 the live view window for `nanokvm viewer` (Tk, read-only)
│   ├── config.py                 settings: nanokvm.toml, overridden by NANOKVM_* environment variables
│   ├── capture.py                HDMI capture (OpenCV DirectShow; NanoKVM matched by USB ID only)
│   ├── hid.py, protocol.py, keys.py   keyboard/mouse over the CH9329-compatible serial protocol
│   ├── geometry.py               coordinates, change detection, calibration
│   ├── simulator.py              simulated locked computer for tests
│   ├── saved_images.py           deletes saved screenshots after saved_image_minutes
│   └── cli.py                    the `nanokvm` command
├── tests\                        202 tests (simulator, no hardware needed)
├── tools\                        hackathon test scripts (Glimmer: section 5a), not used by the bridge yet
│   ├── run-glimmer.ps1           starts llama-server with Glimmer on 127.0.0.1:8001
│   ├── glimmer_smoke.py          Glimmer smoke test on a rendered dummy C++ screen
│   ├── spark_smoke.py            Meta Model API smoke test with dummy text (key: .run\meta_api_key)
│   ├── capture_frame.py          saves full-resolution NanoKVM frames for the A/B (dummy content only)
│   ├── glimmer_ab.py             scores Glimmer's reading of those frames against ab_screens\
│   ├── ab_screens\               dummy C++ files and build errors to show for the A/B
│   ├── downloads\                the two llama.cpp zips that llama.cpp\ was unpacked from
│   └── llama.cpp\                llama.cpp b10976 (Windows CUDA 13.3 build)
├── models\                       Glimmer weights: Meta's here, Unsloth's in unsloth\
├── .cache\nv\                    NVIDIA's compiled-kernel cache for llama.cpp (section 5a)
├── .venv\  .uv\                  Python and packages (local to this folder)
└── .run\                         token, keys, logs, screenshots, calibration (created at runtime)
```

- **Run the tests:** `.venv\Scripts\python.exe -m pytest -q`
- **Try it without the NanoKVM:**

  ```powershell
  $env:NANOKVM_SIMULATE = "1"; $env:NANOKVM_PORT = "8790"
  .venv\Scripts\nanokvm.exe viewer        # a live window showing a simulated screen
  .venv\Scripts\nanokvm.exe stop
  Remove-Item Env:NANOKVM_SIMULATE, Env:NANOKVM_PORT   # this terminal uses the real NanoKVM again
  ```

The keyboard/mouse protocol follows Sipeed's official NanoKVM-USB app (v1.1.4): CH9329 frames
`57 AB 00 CMD LEN DATA SUM` at 57600 baud, with the absolute mouse on a 0–4095 grid. Status as of
2026-10-29: tested with the simulator, and on real hardware on 22 and 29 Oct: capture works at
1920x1080 and about 30 fps, the `nanokvm viewer` window shows the locked computer's screen live
(median delay from capture to window 37 ms), and the keyboard/mouse chip answers at 57600 baud.
Keyboard and mouse on the locked computer (cable 4) aren't tested yet.
