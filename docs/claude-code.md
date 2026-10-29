# Claude Code bridge (built before the event)

Before the hackathon, between 7 and 25 Oct 2026, this project was a bridge that let **Claude Code in
VS Code on the AI computer** see the locked computer's screen and, when allowed, use its keyboard and
mouse, through an MCP server called `locked-computer`. It is in the `pre-event-baseline` commit, it
still works, and it shares the NanoKVM service with the Meta-model pipeline. It is **not part of the
hackathon entry**: see the [README](../README.md) for the entry and [HACKATHON.md](../HACKATHON.md) for
what was built when. During the event it changed only for the renames (the two computers, and the MCP
server, now `locked-computer`), the `locked_os` setting, which tells Claude the locked computer's OS
and shortcuts, a pointer to the viewer window in `screen_status`'s reply, and, on 29 Oct, the rules
in section 3 that keep it out of `.run\`.

## 1. Connect to Claude Code in VS Code

The server is registered **for this folder** in `.mcp.json`, and is already connected and approved.

1. In VS Code, open the folder **`E:\meta_hackathon`**.
2. Start a **new** Claude Code conversation. After changing `nanokvm.toml`, run `nanokvm stop` first.
3. Ask: *"Take a screenshot of my locked computer."* Type `/mcp` to see the server's status.

## 2. How Claude sees the screen

Claude doesn't watch continuous video; it looks at an image each time a tool returns one. This setup
gets as close as Claude Code allows:

- **Always current.** Every screenshot is the current frame (~0.1 s old).
- **Reacting to changes.** `wait_for_change` makes Claude respond as soon as the screen changes.
- **Live view for you.** `nanokvm viewer` shows the screen to you live
  ([README section 7](../README.md#7-watching-the-screen-yourself)).

## 3. Approvals and what goes to Anthropic

| Protection | How |
|------------|-----|
| Claude acts only when you allow it | There are **two gates**. **Physical:** the keyboard/mouse only work while cable 4 is plugged in; unplug it and the setup is view-only. **Per action:** Claude Code asks you before **every** click or keypress, even in auto mode (`confirm_actions = true`). |
| One approval to share the screen | Claude's screen tools work only after Claude calls `start_screen_sharing`, which **always** asks you, even in auto mode. Once you approve, that conversation can take screenshots, zoom and wait for changes without further prompts. Sharing lasts as long as Claude asks for in that call: **60 minutes** by default (`share_minutes`), at most 480, so check the number in the prompt. It ends sooner if you say stop and Claude calls `stop_screen_sharing`, or when the service stops. When sharing is off, the service refuses captures requested by Claude's tools. The `nanokvm` command's `screenshot`, `watch` and `diag` and the live views aren't gated by this ([section 5](#5-tools-claude-gets)); `nanokvm read`, `ask` and `edit` ask at the console to share the screen with Muse Glimmer. |
| Claude sees only one screen | Only Monitor A (the screen wired through the NanoKVM) is captured. The locked computer's built-in screen and everything on the AI computer's desk are never seen. |

**What leaves for Anthropic:** every image Claude gets is sent to Anthropic as part of the
conversation. While sharing is on, that means screenshots, zooms, `wait_for_change` results and the
screenshot after each action. Keep anything you may not share off Monitor A while sharing is on. Use
this bridge only with the approval of whoever owns the locked computer and its data.

**Keeping Claude out of `.run\`.** That folder holds what Claude must not see: the screenshots the
`nanokvm` command saves, the audit log of what was sent to Muse Spark (`outbound.log`, with redacted
screen text and Spark's replies), your redaction terms (`redact_terms.txt`), the API keys and the
service token. Anything Claude reads goes to Anthropic, so since 29 Oct Claude Code in this folder is
refused when it tries to open them:

- **Deny rules** keep its file tools (Read, Edit, Write, NotebookEdit, and as far as Claude Code can,
  Grep and Glob) out of `.run\`. They also stop the shell file commands Claude Code recognises, such
  as `cat`, `head`, `tail`, `sed`, `tee` and redirections, but not other programs.
- **A hook**, `tools\guard_run_dir.py`, refuses any Bash, PowerShell or Monitor command that names
  the `.run` folder, in every permission mode, bypass included. It also refuses a recursive `grep`,
  `rg` with `--no-ignore` or `-u`, and `find` with `-exec` unless they exclude the folder
  (`--exclude-dir=.run`), because those reach it without naming it. Its message tells Claude how to
  do the same thing safely, for example a commit message that mentions `.run` goes through
  `git commit -F` with a file.

The hook reads only the command line, so it doesn't catch a script saved to a file and then run, code
that uses the project's own `Config.run_dir`, or a command that copies or walks the whole project
another way (`tar`, `cp -r`, a PowerShell `Get-ChildItem -Recurse` pipeline). Programs Claude runs
still read the files they need, such as the `nanokvm` command and a local git pre-commit hook (git
hooks aren't in the repo). So this prevents mistakes; it isn't a barrier against a
determined program. The service token can still reach Claude: `screen_status` puts it in the
live-view URL it returns ([section 5](#5-tools-claude-gets)), and so do `nanokvm viewer --no-open`
and `--browser` if Claude runs them (`nanokvm status` hides it).

The rules live in `.claude\settings.local.json`, which isn't in the repo. To use them in your copy,
add this to that file (the last two deny rules name this folder, so change them if yours is
elsewhere). The hook needs Git Bash and the project's `.venv`, and Claude Code 2.1.295 or later for
`onFailure`: if the hook itself can't run, for example when `.venv` breaks after the folder is renamed,
every shell command is refused ("blocking because onFailure is "block""). Then rebuild the venv in a
terminal outside Claude Code ([README section 5](../README.md#5-install-already-done)), or delete the
`hooks` block from `.claude\settings.local.json`, and start a new conversation. Claude Code normally
picks up settings edits at once; if a change doesn't show in `/hooks` after a few seconds, start a new
conversation.

```json
"permissions": {
  "deny": [
    "Read(/.run)", "Read(/.run/**)", "Edit(/.run)", "Edit(/.run/**)",
    "Read(//e/meta_hackathon/.run/**)", "Edit(//e/meta_hackathon/.run/**)"
  ]
},
"hooks": {
  "PreToolUse": [{
    "matcher": "Bash|PowerShell|Monitor",
    "hooks": [{
      "type": "command",
      "command": "\"$CLAUDE_PROJECT_DIR/.venv/Scripts/python.exe\" -I -S \"$CLAUDE_PROJECT_DIR/tools/guard_run_dir.py\"",
      "shell": "bash", "timeout": 30, "onFailure": "block"
    }]
  }]
}
```

## 4. Asking Claude

The first time Claude needs to look, it asks to **share the screen**. Approve once, and it can then
look as often as it needs during that sharing session. Say "stop sharing" when you're done:

- *"Look at my locked computer and explain the error dialog."* Claude uses `screenshot`, then `zoom`
  for small text.
- *"Wait until the build on the locked computer finishes, then summarise it."* Claude uses
  `wait_for_change`.
- *"Open Notepad on the locked computer and type this draft."* This needs cable 4. Claude uses
  `press_keys` and `type_text`, and you approve each step.

**Reading code.** With the VS Code settings in
[README section 4](../README.md#4-locked-computer-settings), Claude takes a `screenshot` and uses
`zoom` on lines that are hard to read. To read a whole file, press **Page Down** after Claude has read
each screen and called `wait_for_change` again; scrolling fast doesn't work, because Claude only sees
the screen when it settles. With cable 4, Claude can navigate with VS Code's shortcuts (**Ctrl+P**
open a file, **Ctrl+G** go to a line, **Ctrl+F** find, **Ctrl+Shift+O** go to a symbol, **F12** go to
the definition, **Page Down**), and you approve each keypress. Before Claude types code, switch
VS Code's typing helpers off as README section 4 says.

**Your own keyboard and mouse at the same time.** They keep working. Don't touch the mouse while
Claude clicks (you share one pointer), and don't hold Shift, Ctrl or Alt while Claude types (Windows
shares modifier keys across keyboards). Press **Esc** in Claude Code to stop an action and release
all keys, or unplug cable 4.

## 5. Tools Claude gets

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

"Allowed" tools are pre-approved in `.claude\settings.local.json`. "While sharing" is checked by the
service, but only for Claude's tools, which send an ID made when the `locked-computer` MCP server
starts (once per Claude Code session). Without an approved share for that ID, their captures are
refused; actions still work but return no screenshot. If a session continues into a new conversation
(for example with `/clear`), the share may carry over, so ask Claude to stop sharing before you clear.
Requests without an ID, from the `nanokvm` command (`screenshot`, `watch`, `diag`) and the live view,
are not gated: they need only the token in `.run\token`. Any program on the AI computer that can read
the token can make them, including a shell command run by an AI.

"Asks you, always" and "asks you, every time" are Claude Code permission prompts, shown even in auto
mode because those tools are marked `anthropic/requiresUserInteraction`; for the keyboard/mouse tools,
that comes from `confirm_actions = true`. Plain "asks you" is Claude Code's ordinary prompt, which
auto mode may skip. These prompts don't apply to other AI tools or to programs that call the service
directly. With `control = false`, the keyboard/mouse tools disappear and the service refuses
keyboard/mouse requests from any program.

**Until approvals are owned by the service** (change 1 in
[the hackathon plan](hackathon-plan.md#3-planned-code-changes), not built yet), use Claude Code's
default permission mode (not auto mode) in this folder while the locked computer shows anything you
may not share, and set `control = false` when Claude doesn't need to act.

## 6. Settings only Claude uses

These go in `nanokvm.toml` like the others ([README section 9](../README.md#9-settings-nanokvmtoml)).

| Setting | This setup | Effect |
|---------|------------|--------|
| `confirm_actions` | `true` | Claude Code asks before every keyboard/mouse action, even in auto mode. The default is `false`, so keep it set. |
| `shot_width` | 1456 | Screenshot width Claude sees. 1456 keeps code legible; it's capped automatically so Claude never gets a downscaled image. |
| `jpeg_quality` | 80 | JPEG quality of Claude's screenshots (`zoom` uses at least 90) |
| `share_minutes` | 60 | Default length of a screen-sharing session; Claude can ask for 1–480 minutes |
| `post_action_ms` | 1500 | Longest wait, in milliseconds, for the screen to react after an action before its screenshot |

`locked_os` also tells Claude which shortcuts to use (on a Mac, `cmd` where Windows uses `ctrl`).

## 7. Troubleshooting

| Symptom | Fix |
|---------|-----|
| Claude says the `locked-computer` tools don't exist | Open `E:\meta_hackathon` in VS Code and start a new conversation. Check `/mcp`. If it shows the server failed, run `.venv\Scripts\python.exe -m nanokvm_mcp.mcp_server` in a terminal to see the error (Ctrl+C quits); a bad value in `nanokvm.toml` is the usual cause. |
| `coordinate space changed … take a new screenshot` | Expected after `calibrate` or a resolution change. |
| `keyboard/mouse busy` | Another conversation, `nanokvm edit`, or a long `type_text` is using it. Wait, or call `release_all`. |
| Letters come out in the wrong case | Caps Lock is shared with the locked computer's own keyboard. Typing allows for it only when the NanoKVM reports it, so switch Caps Lock off. |
| Clicks land on the wrong screen | Make Monitor A the **main display** ([README section 4](../README.md#4-locked-computer-settings)). If clicks are offset on the right screen, ask Claude to run `calibrate`. |
| Screenshot text is blurry | Run Monitor A at 1920x1080. Ask Claude to use `zoom`. |
| The token went out in a `screen_status` result | It never changes by itself. To replace it, run `nanokvm stop` and delete `.run\token`; the next start makes a new one. |
