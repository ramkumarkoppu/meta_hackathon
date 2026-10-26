# Hackathon plan: Meta models for the Work laptop bridge

This page plans how this project could use Meta's AI models and enter the **Meta Global AI Developer
Hackathon** ([event page](https://cerebralvalley.ai/e/meta-ai-developer-hackathon)), run by Meta and
Cerebral Valley. **Our application was accepted on 20 Oct.** We decide go or no-go on Mon 26 Oct
([section 7](#7-schedule-and-status)). **Nothing here is built yet** unless its status says *Done*,
*Installed*, *Set up* or *ready*. Facts were checked on **2026-10-14** and updated on
**2026-10-22**; the local Glimmer setup and its measurements are as of **2026-10-24**. Rules, dates
and prices can change.

- **The idea.** Approved AI help for a computer where nothing can be installed. Meta's open-weights
  **Muse Glimmer** reads the screen on the Personal laptop's own GPU, so in local-only mode no screen
  image leaves the desk; only the text it returns goes on to the AI you chat with. **Muse Spark**, a
  Meta cloud model, works the mouse and keyboard through Meta's computer-use tool on a stand-in PC
  for the demo, and is sent that PC's screenshots. A person approves every action, and unplugging
  the NanoKVM's keyboard/mouse cable (cable 4 in the [README wiring](../README.md#3-wiring-for-this-desk))
  stops all control.
- **Theme:** "Build for Your World", a problem we face first-hand.
- **How the bridge works today:** see the [README](../README.md).

---

## Contents

1. [The event](#1-the-event)
2. [Which Meta model does what](#2-which-meta-model-does-what)
3. [Planned code changes](#3-planned-code-changes)
4. [The Meta agent loop](#4-the-meta-agent-loop)
5. [Data, privacy and IP rules](#5-data-privacy-and-ip-rules)
6. [Install locations](#6-install-locations)
7. [Schedule and status](#7-schedule-and-status)
8. [Open questions and risks](#8-open-questions-and-risks)
9. [What's new compared with similar projects](#9-whats-new-compared-with-similar-projects)

---

## 1. The event

Virtual and worldwide, with a $1,000,000 prize pool. Checked 2026-10-14 against the event page and
its *Application and Event Overview and Event Waiver* (dated 13 Oct 2026). Updated 2026-10-22 from the
event page, Meta's invitation email (13 Oct) and Cerebral Valley's acceptance email (20 Oct).

| Item | Detail |
|------|--------|
| Our entry | **Indie developer** track, solo. Judged on "creativity, technical ambition, and whether it works". **Application accepted on 20 Oct.** |
| Theme | Two themes (kickoff, 26 Oct): **Build for Your World** ("Bring Meta's models into your domain. Solve a problem you know firsthand, free up time and mental space, and make something possible that wasn't before") and **Push the Frontier** (software, robotics, manufacturing, energy, materials). **We enter Build for Your World.** Machines that can't run AI could suit Push the Frontier, but only as read-only reading with a person deciding: Meta's Acceptable Use Policy bars operating critical infrastructure or heavy machinery. |
| Indie prizes | $150k, $100k, $75k, $50k and $25k, plus up to $50k of bonus awards across the event |
| Credits | $150 of Meta Model API credits per accepted participant, for the 10 days (confirmed in Meta's own invitation email). Meta's terms say promotional credits can expire and aren't refundable, and its IP indemnity doesn't cover use paid for with them. |
| Partner credits | For *select* participants only, so not guaranteed, and only for running Meta's open-weights models (such as Muse Spark Open) on their clouds: Baseten $100 plus a 2M tokens-per-minute rate limit, Modal $100 (its Endpoints, serverless GPUs and sandboxes), and Fireworks $25 plus a workshop. Use them only with the stand-in PC, never with Work-laptop content. |
| Official Rules | Promised before 26 Oct; still not published on 22 Oct |
| Still unknown | Whether existing code may be used, whether other AI providers (such as Claude) may appear in the project, and the submission format (the submission form has fields for a GitHub repo, a live project, a demo video and a description) |

**Key dates.** The acceptance email gives the build window as Mon 26 Oct 12:00 PM to Wed 4 Nov
9:00 PM, **GMT-5**. In UK time (GMT from 25 Oct) that is 17:00 on 26 Oct to 02:00 on Thu 5 Nov.

| UK time | Event time (GMT-5) | What |
|---------|--------------------|------|
| Tue 20 Oct | | **Our application accepted** |
| Mon 26 Oct, 17:00 | Mon 26 Oct, 12:00 PM | Build starts; kickoff livestream with Meta Superintelligence Labs |
| Tue 27 Oct – Wed 4 Nov | | Live workshops and office hours (on Discord) with the engineers who built the models |
| **Thu 5 Nov, 02:00** | Wed 4 Nov, 9:00 PM | **Submissions due** |
| Wed 4 – Sun 8 Nov | | Judging |
| Mon 9 Nov | | Live finals and winners announced |

**Judging**, and how this plan aims to score:

| Criterion | Weight | How this plan scores |
|-----------|--------|----------------------|
| Impact | 30% | AI help on a computer where nothing can be installed, with a local-only mode that keeps screen images on the desk |
| Use of Meta AI tooling | 25% | Two Meta models in two roles: Glimmer reads the screen locally, with DFlash and a measured thinking cap (and a fine-tune if real screens need it); Spark reasons and codes from redacted text only, proposing edits through function calling and strict JSON. Every edit needs human approval. "Beyond a basic integration". (Spark's `computer` tool is not used: it needs screenshots sent to Meta.) |
| Technical depth and execution | 20% | Real HDMI capture and USB keyboard/mouse, coordinate mapping, approvals owned by the service, local inference |
| Demo | 25% | A live demo on a stand-in PC; the video shows the approvals, local reading, the agent at work and the cable kill switch |

## 2. Which Meta model does what

Checked 2026-10-14 against Meta's [models](https://dev.meta.ai/docs/models) and
[computer use](https://dev.meta.ai/docs/computer-use) docs. Prices are per million tokens:
input / cached input / output.

| Model | Runs on | Role | Cost |
|-------|---------|------|------|
| **Muse Glimmer 30B** (open weights, Apache-2.0) | The Personal laptop's RTX 5090 (24 GB), through llama.cpp | Reads code and describes the screen ([change 3](#3-planned-code-changes)). The only Meta model that can read real work content without sending images off the laptop; its text still goes on to the AI you chat with ([section 5](#5-data-privacy-and-ip-rules)). | Free. About 19 GiB of GPU memory with the vision part at a 131,072-token context, or 20.5 GiB with the optional drafter (Meta's figures). |
| **muse-spark-1.3** | Meta Model API | The computer-use agent ([section 4](#4-the-meta-agent-loop)) | $1.25 / $0.15 / $4.25 |
| **muse-wonder-1.0** (preview) | Meta Model API | Stretch: hard multi-step tasks. As a preview, it may change during the event. | $4.00 / $0.20 / $20.00 |
| **SAM 3.1** | Meta Model API, or local | Stretch: finding UI elements, only if an offline test shows it beats the models above | $2.50 per 1,000 images |

About Glimmer (figures reported by Meta, except the last point):

- **Finding things on screen:** 75.4 on ScreenSpot-Pro, using a crop-and-zoom loop like our `zoom` tool.
- **Computer use:** 65.9 on OSWorld-Verified. Meta reports 80.8 for Muse Spark 1.1; for
  muse-spark-1.3, the agent here, it publishes only OSWorld 2.0 scores, which aren't comparable.
- **Prompt injection:** attacks succeeded 28.4% of the time in Meta's test, so treat its output as
  untrusted text, like the screen itself.
- **Limits:** one tool call per turn, and it reasons before answering, which makes it slower. For
  reading, each request here caps its thinking ([section 8](#8-open-questions-and-risks)).
- **Measured here (24 Oct):** on a dummy 1920x1080 code screen, every read got all 27 lines right, in
  6.5–7.0 s with the Personal laptop in Turbo mode on its 240 W charger, and in about
  48–65 s in Silent mode, in reads made without the thinking cap ([section 7](#7-schedule-and-status)).
  How well it reads a real captured screen is still untested; that is the next thing to measure.

**Not used:**

| Model or product | Why not |
|------------------|---------|
| `-contributor` model IDs | Meta trains on their inputs, and its terms forbid confidential data there |
| Muse Code subscription, including Muse Wonder Full (only inside Muse Code; not the `muse-wonder-1.0` API model above) | The subscription key works only inside Muse Code, it defaults to Contributor mode, and the installer writes outside this folder |
| Muse Image, Muse Voice Transcribe | No role in reading or controlling a screen |
| Llama 4 | Doesn't fit in 24 GB of GPU memory, and is weak at tool calling |
| Muse Spark Open (open weights, 816B parameters, 42B active; released 15 Oct 2026) | Far too big for this laptop: the smallest community build (1-bit GGUF) is 176 GB, against 24 GB of GPU memory plus 64 GB of RAM, and Meta's own builds need at least 700 GB of GPU memory. Third-party hosts would be another cloud. The $150 Meta credits don't cover them. The partner credits do, if we get them, so a stretch demo on the stand-in PC is possible, using tool calling on the 0–1000 grid because Meta's `computer` tool is only on its own API. |

## 3. Planned code changes

Change 1 fixes today's bridge and is needed whether or not we enter, so it isn't held back for the
event. Changes 2–7 are the hackathon work, built from 26 Oct; only change 2's model server is set up
already, for the Glimmer tests. Each change comes with simulator tests, so the tests still need no
hardware, GPU or API key.

| # | Change | Main files | Status |
|---|--------|------------|--------|
| 1 | **Approvals owned by the service** (first). Every screen share, keyboard/mouse action and `calibrate` needs a click in an approval window that the service itself shows (Tk, which ships with the project's Python). `release_all` is exempt: pressing Esc sends it automatically, it only lifts keys and buttons and stops the action in progress, and it also cancels any approval still waiting. Each approval has a deadline shorter than the caller's timeout (today 15 s for a share and about 31 s for a click, so `client.py` must allow longer); when it passes, the window closes and the request is refused, so a late "yes" can never act on whatever window has focus by then. The token stays out of tool output, and the live view and the `nanokvm` command get the same gate or their own credential. | `service.py`, new `approval.py`, `mcp_server.py`, `client.py`, `cli.py` | Planned |
| 2 | **Local model server.** llama.cpp's `llama-server` runs Glimmer, listening on 127.0.0.1 only. | `tools\run-glimmer.ps1`, `models\` | Set up for the tests ([section 6](#6-install-locations)) |
| 3 | **`read_screen_text` and `describe_screen` tools, and a `nanokvm read` command.** They send the current frame, or an area at full HDMI resolution as `zoom` does, to Glimmer and return text only. Gated by screen sharing, like `screenshot`. The command lets the demo show local reading without any other AI. | `service.py`, `mcp_server.py`, `client.py`, `cli.py` | Planned |
| 4 | **`local_only` setting**, enforced by the service. Image tools refuse and point to `read_screen_text`, actions return no screenshot, the `nanokvm` command stops saving screenshots, and the Meta agent won't start. | `config.py`, `service.py`, `mcp_server.py`, `cli.py` | Planned |
| 5 | **Meta agent loop**, `nanokvm agent` ([section 4](#4-the-meta-agent-loop)). Needs change 1 first. | new `meta_agent.py`, `cli.py` | Planned |
| 6 | Stretch: escalate hard steps to Muse Wonder; a SAM 3.1 `find_element` tool, only after an offline test | | Stretch |
| 7 | **Demo** on a stand-in PC with a dummy C++ project, never the real Work laptop | | Planned |

**Why change 1 comes first.** Today the screen-share approval and the per-action approvals are both
Claude Code prompts. Other AI tools, including the planned Meta agent, wouldn't get them, and the
service accepts any request that carries its token, including one that turns sharing on.

When each change lands, update the README to match: the intro and sections 1, 2, 5, 7, 8, 9, 10,
11, 12 and 13.

## 4. The Meta agent loop

*Planned (change 5); not built yet.*

`nanokvm agent "<task>"` will run Meta's computer-use loop: the Responses API at
`https://api.meta.ai/v1`, with `tools=[{"type": "computer"}]` and `muse-spark-1.3`. It sends a
screenshot, receives actions, asks you before each one, carries it out through the service, and
sends a new screenshot, until the model stops asking for actions. Meta's coordinates are pixels of
the screenshot it was sent, which is the service's own coordinate space: 1456x819 for a 16:9 picture,
smaller at other shapes (1370x856 at 16:10).

| Meta action | Our action | Conversion |
|-------------|------------|------------|
| `click` | `click` | Left, right, and middle (Meta's `wheel`); `back` and `forward` are refused |
| `double_click` | `click` with `count` 2 | |
| `move` | `move` | |
| `drag` (a path of points) | `drag` | First and last point only: our drag goes straight from start to end |
| `scroll` | `scroll` | One direction per call; Meta's deltas become 1–50 notches (scale set on the stand-in PC) |
| `keypress` (a list of keys) | `keys` | Joined with `+`; a few key names need mapping |
| `type` | `type` | Split into chunks of at most 1,500 characters |
| `wait` | (none) | Pause, then take a new screenshot |
| `screenshot` | `screenshot` | Use the width and height returned with each screenshot. Keep `shot_width`, `display_aspect` and the calibration unchanged during a run. |

Rules for the loop:

- **You approve every action.** Meta's `pending_safety_checks` are acknowledged only after you agree.
- **One action at a time.** A response can hold several actions. After any action that changes the
  screen, the loop takes a new screenshot and asks again.
- **Coordinates are clamped** to the screenshot. Meta allows the full width and height; our service
  accepts up to one pixel less.
- **Standard model only.** The loop stops if a response reports a different model from the one it
  asked for.
- **`store: false`.** Meta doesn't store the conversation between turns (it may still keep content
  for safety review; see [section 5](#5-data-privacy-and-ip-rules)), so the loop resends the history
  itself, copying each `meta_safety_replay_receipt` back unchanged. Meta accepts up to 29 call/output
  pairs this way, so a run ends after 29 turns; a request also carries at most 50 images.
- **Computer tool only.** Outside this tool, Meta's models give positions on a 0–1000 grid instead of
  pixels, which would send clicks to the wrong place.
- **Its own share.** The loop sends its own client ID (the ID that tells the service which screen
  share a request belongs to), so it needs its own approved share, like each Claude Code session.

## 5. Data, privacy and IP rules

| Rule | Why |
|------|-----|
| Work-laptop content goes to a cloud model (Anthropic's today, Meta's later) **only with the approval of whoever owns the laptop and its data**. Until Meta's models are approved too, the Meta agent works only on the stand-in PC. | Meta's Acceptable Use Policy forbids using its models to defeat security controls or to act on systems against their own terms, and requires human oversight of consequential actions. The same approval applies to Claude today ([README section 2](../README.md#2-secure-mode-how-its-configured)). |
| Use **Standard** model IDs only, never `-contributor` ones. | Meta trains on Contributor inputs, and its terms forbid confidential data there. Meta also warns that requests can be routed to a Contributor model, hence the model check in [section 4](#4-the-meta-agent-loop). |
| While the account is paying with the **$150 of free credits**, send Meta **dummy or stand-in content only**: development, tests and the demo. Never send Work-laptop text, even redacted. For real work, first click *Stop using free credits* in Billing and pay as you go. | Meta's Billing page says: "While using free credits your content may be used for product improvement" (seen 26 Oct). The employer approved redacted text under Standard terms, which don't allow training on it (Terms of Service §5.1). Free-credit traffic isn't covered by that. |
| Send `store: false`. | The Responses API stores conversations by default. Even in Standard mode, Meta may keep content for safety and abuse review; zero retention is only for organisations Meta's sales team approves. |
| Frames of the Work-laptop screen read by Glimmer are **never written to disk**. They stay in memory, are discarded as soon as Glimmer has read them (before anything is sent to Spark), and the model server's slot is cleared after each read. The audit log records only a hash of each frame. | Deleting a file on an SSD doesn't erase it, a crash between saving and deleting would leave the image behind, and any program or AI file tool on the laptop can read files in the project folder. Spark gets only text, so it never needs the frame. |
| For real work, set `local_only = true` ([change 4](#3-planned-code-changes)). | Claude's tools and the Meta agent then get no screen images. **The text Glimmer returns still goes to Claude**, and so to Anthropic. |
| Nothing from work in the application form, the repo, the demo video, workshops, office hours or Discord. | The event waiver makes everything shared non-confidential, gives the organisers a perpetual licence to use it, and allows recording and reuse of screen shares. |
| Submit only work we have every right to submit. | The waiver requires the submission to be original and solely ours, and not to breach anyone's IP or contract rights. |

## 6. Install locations

*Installed by 24 Oct, apart from the Meta API key.* Everything stays inside `E:\work_laptop`, like
the rest of the project ([README section 5](../README.md#5-install-already-done)).
[README section 5a](../README.md#5a-local-muse-glimmer-hackathon-tests) has the commands to start and
check Glimmer.

| What | Location | Notes |
|------|----------|-------|
| llama.cpp release **b10976**, the portable Windows build for CUDA 13.3 (b10353 or later: older builds can't load Glimmer at all) | `tools\llama.cpp\` | Unzip both the CUDA 13 build and its "CUDA 13.3 DLLs" zip; there's no installer. Check that `llama-server`'s startup log lists the RTX 5090. The CUDA 13 build has native kernels for this GPU; the CUDA 12 build would have to compile them on first run. Stay on b10976 until a newer build is re-benchmarked ([section 8](#8-open-questions-and-risks)). |
| Meta's Glimmer weights from [meta-models/Muse-Glimmer-30B-GGUF](https://huggingface.co/meta-models/Muse-Glimmer-30B-GGUF): `Muse-Glimmer-30B-KQuant-17GB-Q4_K_M.gguf` (16.76 GB), `mmproj-Muse-Glimmer-30B-Q4_K_M.gguf` (1.40 GB, the vision part) and `dflash-Muse-Glimmer-30B-Q4_K_M.gguf` (1.63 GB, the DFlash drafter, for speed) | `models\` | SHA-256-verified. The launcher uses these weights with `-Model meta`, and the drafter with either set of weights. The vision part is byte-identical to Unsloth's `mmproj-kquant.gguf`. |
| Unsloth's Glimmer weights: `Muse-Glimmer-30B-UD-Q4_K_XL.gguf` (15.88 GB) and `mmproj-Muse-Glimmer-30B-BF16.gguf` (3.85 GB, the vision part in BF16) | `models\unsloth\` | SHA-256-verified. The launcher's default. This GGUF carries Meta's old chat template ([section 8](#8-open-questions-and-risks)). |
| Launcher | `tools\run-glimmer.ps1` | Starts `llama-server` with Glimmer (below) |
| Smoke tests (dummy content only) | `tools\glimmer_smoke.py`, `tools\spark_smoke.py` | `glimmer_smoke.py` renders a dummy C++ file as a 1920x1080 image (nothing from either laptop's screen), has Glimmer transcribe it, and reports accuracy, speed and GPU memory. `spark_smoke.py` tests the Meta Model API with dummy text: `/v1/models`, a chat completion with `store: false`, a reply that must name exactly `muse-spark-1.3`, strict `json_schema` output, and streaming with usage. Any failed check stops it with exit code 1, and a run costs well under one cent. It never prints the key, and refuses a malformed key file and any redirect. |
| Real-screen A/B (dummy content only) | `tools\capture_frame.py`, `tools\glimmer_ab.py`, `tools\ab_screens\` | `capture_frame.py` saves full-resolution frames from the NanoKVM as PNGs (`--interactive` asks for each A/B screen in turn); stop the service first (`nanokvm stop`), since it holds the capture device. `glimmer_ab.py` scores Glimmer's transcription of those frames against the dummy files in `ab_screens\` (`ring_buffer.cpp`, `config_parser.cpp`, `long_lines.cpp`, `build_errors.txt`) and prints only metrics unless given `--show-diffs`. |
| NVIDIA's compiled-kernel cache (`CUDA_CACHE_PATH`, which the launcher sets) | `.cache\nv\` | llama.cpp's own download cache isn't used: the launcher runs it with `--offline` and clears every `LLAMA_*` variable, `LLAMA_CACHE` included. |
| Key for the local model server | `.run\llama_api_key` | Random; the launcher makes it on first start, and the Glimmer test scripts read it. Needs a deny rule, like the Meta key. |
| Meta Model API key | `.run\meta_api_key` | Not created yet. Save it there yourself as the file's only line (`LLM_<id>_<secret>`); never paste it into a chat, commit or publish it. Needs a deny rule so AI tools can't read it ([section 8](#8-open-questions-and-risks)). |

**The launcher**, `tools\run-glimmer.ps1`, starts `llama-server` with Unsloth's weights, the BF16
vision part and the DFlash drafter (draft length 15), as model `muse-glimmer` on
`http://127.0.0.1:8001`, with one request slot and a 32,768-token context. Ctrl+C stops it.

| Option | Effect |
|--------|--------|
| `-Model meta` | Meta's own Q4_K_M weights, for the A/B |
| `-Projector bf16` or `q4km` | The vision part. Default: `bf16` with Unsloth's weights, `q4km` with Meta's. |
| `-NoDFlash` | Runs without the drafter |
| `-DraftMax N` | Drafts up to N tokens per step, 1–15. Default 15, the most Meta's drafter allows (its block size is 16). |
| `-Ctx N` | Context length, default 32768. One slot and 32K keep the whole model on the GPU; the server's own default (4 slots of 131K) pushed work to the CPU, at about 5 tokens/s. |
| `-Port N` | Port, default 8001. It always listens on 127.0.0.1 only. |

What else it does:

- **Loads the files without memory-mapping them** (`--load-mode none`): from this exFAT drive a cold
  start takes 72 s instead of 130 s.
- **Keeps prompts and screens private.** No web UI, no cross-site browser access, no `/slots`
  endpoint, no model downloads (`--offline`), no prompt cache or context checkpoints copied to RAM,
  and no log file; the one slot still holds the last request until the next replaces it. Never add
  `-v`, `-lv 5` or `--log-prompts-dir`: they log prompts and screenshots. `--offline` doesn't stop the
  server fetching an http(s) image URL sent in a request, so clients send images only as `data:` URIs.
- **Refuses to start** if the key file is empty or malformed (`llama-server` would then run without
  any key), or if a llama.cpp `config.ini` exists under `%ProgramData%` or `%APPDATA%` (it would
  change the server's settings). It clears every `LLAMA_*` and `AIP_*` variable and
  `MTMD_BACKEND_DEVICE` for the same reason.
- **Warns** when the laptop is on battery, on a USB-C (PD) power plan or in Silent mode, which all
  hold the GPU to about 55 W ([section 8](#8-open-questions-and-risks)).

Not used: Ollama (it always writes to the Windows user profile), conda, global installs, and the
Muse Code installer. Nor WSL with vLLM or SGLang: WSL has the same driver and power limits as native
Windows, the only 4-bit Glimmer build for vLLM or SGLang with the vision part is 22.2 GiB, and
Unsloth's Q4_K_XL beats NVFP4 on perplexity.

## 7. Schedule and status

Status as of 2026-10-24.

| By (UK time) | Task | Status |
|--------------|------|--------|
| Done | Meta Model API account created; the API is reachable from this laptop | Done |
| 20 Oct | Apply; Cerebral Valley accepted the application | Done |
| 24 Oct | Install llama.cpp b10976 and Glimmer (Meta's and Unsloth's weights, all SHA-256-verified); write the launcher and the test scripts ([section 6](#6-install-locations)) | Done |
| 24 Oct | Test Glimmer on a dummy C++ screen rendered as an image; measure transcription accuracy and speed (below) | Done |
| Before 26 Oct | Confirm the work is ours to submit (original, solely owned and free of contract limits), as the waiver requires | To do |
| Before 26 Oct | Hardware bring-up with `nanokvm diag` ([README section 9](../README.md#9-first-run-with-the-hardware)) | To do |
| Before 26 Oct | Build change 1 (approvals owned by the service), a fix to today's bridge | To do |
| Before 26 Oct | Test Glimmer through the NanoKVM's capture, with dummy content only: reconnect the NanoKVM, loop the Personal laptop's HDMI into its TARGET end (leave the TARGET USB-C unplugged then), open the `.cpp` files in `tools\ab_screens\` in VS Code and `build_errors.txt` in a terminal, save the frames with `capture_frame.py --interactive` (after `nanokvm stop`) and score them with `glimmer_ab.py`. Compare Meta's and Unsloth's weights, and DFlash on and off. | To do; scripts ready |
| Before 26 Oct | Test the Meta Model API: add a card, create a key and save it as the only line of `.run\meta_api_key` (never in a chat), run `tools\spark_smoke.py`, then send one `computer` request to each model and check the model reported back | To do; smoke test ready, no key yet |
| Before 26 Oct | Claude Code deny rules for the secrets in `.run\` ([section 8](#8-open-questions-and-risks)) | Proposed; waiting for an OK |
| Before 26 Oct | Set up a stand-in PC: anything with HDMI out and a USB-A port, running only dummy content | To do |
| Before the repo goes public | Add a `.gitignore` for `.run/`, `.venv/`, `.uv/`, `.cache/`, `models/`, `tools/llama.cpp/`, `tools/downloads/`, `.pytest_cache/`, `__pycache__/` and `.claude/settings.local.json`, then check that no screenshot, log, token or key would be published | To do |
| Mon 26 Oct | Read the Official Rules; decide go or no-go | Waiting |
| 26 Oct – 4 Nov | Build changes 2–5 (and change 1 if it isn't done yet); record the demo video | Planned |
| Wed 4 Nov, evening | Submit (deadline Thu 5 Nov, 02:00) | Planned |

- **Go if** the Official Rules allow this entry and building on this bridge, the hardware works, and
  Glimmer reads code well enough.
- **Before 26 Oct, no Meta feature code,** in case the rules exclude earlier work. Change 1 fixes
  today's bridge, so it doesn't wait. The launcher and test scripts in `tools\` serve the go/no-go
  tests; the bridge's own code has no Meta features.
- **Two days left before 26 Oct** (as of 24 Oct). Glimmer runs locally and reads a rendered dummy
  screen correctly. Still open: the hardware bring-up and the A/B through the NanoKVM, the Meta API
  key and smoke test, the deny rules, the ownership check, change 1 and the stand-in PC. Do the quick
  checks first (hardware bring-up with the A/B, then the Meta API test). Change 1 can move into the
  build week.
- **Time budget:** about 30–40 hours alongside work. Rough split: approvals 8–10 h (less if done
  before 26 Oct), local reading and `local_only` 6–8 h, agent loop 10–12 h, demo 4–6 h, spare
  2–4 h. Muse Wonder and SAM 3.1 only if time is left.

**Measured on 24 Oct.** Glimmer read a dummy 1920x1080 code screen (rendered by `glimmer_smoke.py`,
not captured) with Unsloth's weights. Every read got all 27 lines right; the smoke test compares lines
with runs of spaces collapsed, so it doesn't check indentation (the A/B's `exact` score does). Speed
depends mostly on the Personal laptop's power settings ([section 8](#8-open-questions-and-risks)):

| Setting | Full-screen read | GPU power |
|---------|------------------|-----------|
| Armoury Crate's **Silent** mode | About 48–65 s, without the thinking cap (the Turbo rows use it) | Held at about 55 W by a software power cap; median clock 1.3 GHz, against 3.09 GHz max |
| **Turbo**, first run (battery at 31%, charging) | 23–26 s, with DFlash at draft length 15 (generation 46 tokens/s) | Still a median 55 W; cause not found |
| **Turbo**, battery at 88% | 6.7–7.0 s: image processing 5.1–5.3 s, generation 131–141 tokens/s | Median 100 W |
| **Turbo**, battery at 9% and charging | 6.5 s, generation 162 tokens/s | Not logged; fast, so a charging battery was not the cause above |

- **Thinking cap.** With the per-request cap, every read produced 223 output tokens; without it,
  279–610 ([section 8](#8-open-questions-and-risks)).
- **DFlash draft length** (Turbo, first run): generation ran at 14 tokens/s without the
  drafter, 23 tokens/s at draft length 3, 42 at 8 and 46 at 15, the launcher's default. A read took
  23–26 s with draft length 15 against 32–34 s without the drafter, with the same 27/27 lines.
- **Image size.** Image processing time scales with pixels (Turbo, full power): 5.1–5.5 s for
  1920x1080, 3.1 s for a 1300x1080 crop and 1.9 s for 1280x720.
- **Server start.** 6 s with a warm file cache. From cold, 72 s with `--load-mode none` (the
  launcher's setting) against 130 s with the files memory-mapped.

**Decided.** Native Windows llama.cpp, not WSL ([section 6](#6-install-locations)). The bridge keeps
its own UVC capture and CH9329 keyboard/mouse code, since Sipeed publishes no NanoKVM-USB SDK.
llama.cpp stays at b10976 until a newer build is re-benchmarked
([section 8](#8-open-questions-and-risks)).

## 8. Open questions and risks

- **Rules.** Whether existing code may be used (this bridge, and the launcher and test scripts in
  `tools\` written for the go/no-go tests), the submission format, and whether other AI providers
  (such as Claude) may appear in the project. The core demo is meant to use Meta models only, so it
  shows Glimmer's reading through `nanokvm read` ([change 3](#3-planned-code-changes)), not through
  Claude.
- **Keeping secrets out of the AI's reach.** The service token, the Meta key, the local model
  server's key and `nanokvm.toml` sit inside the project folder, where an AI with file access can
  read or edit them; an edit could switch off `confirm_actions`. Change 1 makes the service token
  useless on its own, but not the keys or the settings file, so those also need deny rules in the
  Claude Code settings (or, with a change to `spark_smoke.py`, a folder outside the project for the
  Meta key). Deny rules for the secrets in `.run\` are proposed and wait for an OK. No settings edit
  should be able to switch off the service's approval window.
- **Two approvals?** Whether to keep Claude Code's prompts as well as the service's window.
- **Window focus.** Windows may not bring a background process's window to the front. Test this early.
- **Saved images.** The `nanokvm` command saves screenshots in `.run\`, where an AI's file tools can
  read them, but since 25 Oct it deletes each one after 5 minutes (`saved_image_minutes`), and the
  service deletes them all when it stops. Change 4 stops the saving altogether while `local_only` is
  on. Deleting doesn't erase the data from the SSD. `tools\capture_frame.py` also saves
  full-resolution frames and doesn't delete them, so use it only with dummy content.
- **Crash reports.** A crash dump of the model server or the bridge could contain screen text held in
  memory. Since 25 Oct, Windows Error Reporting is switched off for `llama-server.exe`, `python.exe`
  and `pythonw.exe` on the Personal laptop: they are listed under
  `HKCU\Software\Microsoft\Windows\Windows Error Reporting\ExcludedApplications`, for this user only.
  The Windows pagefile can still hold process memory.
- **Glimmer on real screens.** On the rendered dummy screen every read was right, but captures
  through the NanoKVM (video compression, light and dark themes, font sizes, wrapped long lines, a
  terminal) are untested until the A/B runs.
- **Thinking cap.** llama.cpp b10976 ignores `thinking_budget_tokens` and `--reasoning-budget` for
  Glimmer, so each read carries its own cap in the request, built from Glimmer's own thinking tags
  (`NO_THINKING` in `glimmer_smoke.py`, which `glimmer_ab.py` reuses). This relies on how that build
  behaves: the smoke test warns if reasoning comes back, so re-test after any llama.cpp upgrade. Set
  the reasoning strength per request (`chat_template_kwargs`: `reasoning_strength`), never as a phrase
  in a system prompt: Unsloth's GGUF has Meta's old chat template, which then also appends
  "Reasoning strength: high."
- **DFlash accuracy.** llama.cpp issue #27975 reported an accuracy drop with the drafter on ROCm.
  Here (CUDA), reads of the rendered screen were as accurate with it as without, but real screens are
  untested: run the A/B both ways (`-NoDFlash`).
- **Power.** Glimmer is fast only in Armoury Crate's Turbo mode (120 W for the GPU), on the 240 W
  barrel charger, and with GPU Mode Standard or Optimized. Silent mode, battery and USB-C (PD) power
  cap the GPU at about 55 W, and the launcher warns about each. In the first Turbo run the GPU also
  stayed near 55 W, for a reason not yet found: later Turbo runs on the charger were fast, one of them
  at 9% battery. Measured, a read took about 48–65 s in Silent (without the thinking cap), 23–26 s in
  that first Turbo run and 6.5–7.0 s at full power ([section 7](#7-schedule-and-status)). Fn+F5
  switches modes; changing the Windows power plan doesn't. If reads are slow, watch the GPU's power
  draw with `nvidia-smi --query-gpu=power.draw,clocks.gr --format=csv -l 1`. Run the tests and the
  demo in Turbo on the barrel charger.
- **llama.cpp version.** Stay on b10976 until a newer build is re-benchmarked: a later change halves
  image prompt speed with DFlash (llama.cpp issue #29982). At b10976, Glimmer's `response_format`
  `json_schema` isn't enforced, so check any JSON it returns.
- **NanoKVM edition.** Unconfirmed. USB ID `345F:2131` is Sipeed's Standard-edition firmware ID, not
  the 4K one, so check which edition this unit is when it's reconnected.
- **Costs.** Tests before 26 Oct are paid by card (the API smoke test costs well under one cent), and
  the event credits may expire after the 10 days.
- **Novelty.** Similar projects exist ([section 9](#9-whats-new-compared-with-similar-projects)), so the
  pitch rests on the combination.

## 9. What's new compared with similar projects

| Project | What it does |
|---------|--------------|
| [AgentKVM](https://github.com/iamtwz/agentkvm) | An AI agent skill and command-line tool for the same NanoKVM-USB |
| [mcp-serial-hid-kvm](https://github.com/sunasaji/mcp-serial-hid-kvm) | An MCP server for a CH9329 keyboard/mouse adapter plus HDMI capture, with local OCR screen reading |
| [kvm-pilot](https://github.com/DustinTrap/kvm-pilot) | An MCP server, command-line tool and Claude skill that controls machines through network KVMs (PiKVM, GLKVM, BliKVM), BMCs, Intel AMT and SSH, with per-call approvals and vision on Claude or a local model |

What this plan adds is the combination: a Meta open-weights model reading code locally on a laptop
GPU, an approved assistant for a computer where nothing can be installed, approvals owned by the
service, and a physical cable as the kill switch.
