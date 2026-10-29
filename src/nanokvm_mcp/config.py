"""Settings, loaded from ``nanokvm.toml`` in the project folder and then ``NANOKVM_*`` env vars.

Precedence (highest first): environment variable ``NANOKVM_<FIELD>`` > ``nanokvm.toml`` > default.
Example: ``NANOKVM_SERIAL_PORT=COM5`` or, in nanokvm.toml, ``serial_port = "COM5"``.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

# src/nanokvm_mcp/config.py -> project root is two levels above the package directory.
_DEFAULT_HOME = Path(__file__).resolve().parents[2]

# accepted locked_os values (they pick Claude's shortcut hints); the locked side itself needs no
# software, only HDMI out and USB HID
LOCKED_OSES = ("windows", "macos", "linux")


def _parse_ids(text: str) -> list[tuple[int, int]]:
    """'345F:2131,345F:2133' -> [(0x345F, 0x2131), (0x345F, 0x2133)]"""
    out = []
    for part in text.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        vid, _, pid = part.partition(":")
        out.append((int(vid, 16), int(pid, 16)))
    return out


def _parse_bool(text: str) -> bool:
    t = str(text).strip().lower()
    if t in ("1", "true", "yes", "on"):
        return True
    if t in ("0", "false", "no", "off"):
        return False
    raise ValueError(f"not a boolean: {text!r}")


@dataclass
class Config:
    # --- local service (shared by every Claude Code conversation) ---
    port: int = 8765                     # always bound to 127.0.0.1
    idle_exit_min: float = 60.0          # service exits after this long without clients (0 = never)

    # --- HDMI capture (NanoKVM-USB host side, MacroSilicon MS2131 UVC device) ---
    video_ids: str = "345F:2131,345F:2133"  # VID:PID list; the AI computer's own webcam is never matched
    video_index: int | None = None       # explicit DirectShow index override (use with care)
    capture_width: int = 1920
    capture_height: int = 1080
    capture_fps: int = 30
    capture_fourcc: str = "MJPG"

    # --- keyboard/mouse (WCH USB-serial bridge, CH340 or CH343 -> CH9329-compatible HID chip) ---
    control: bool = True                 # False = view-only (no keyboard/mouse tools)
    confirm_actions: bool = False        # True = Claude Code asks before EVERY keyboard/mouse action
    serial_port: str | None = None       # e.g. "COM5"; default = autodetect by VID:PID
    serial_ids: str = "1A86:7523"
    keyboard_layout: str = "us"         # layout set on the locked computer: "us" or "uk" (PC layouts)
    locked_os: str = "windows"           # the locked computer's OS: "windows", "macos" or "linux"
    baud: int = 57600                    # official NanoKVM-USB apps use 57600

    # --- what Claude sees ---
    shot_width: int = 1280               # screenshot width = width of the pointer coordinate space
    jpeg_quality: int = 80
    display_aspect: str | None = None    # e.g. "16:10" if the HDMI picture is stretched to 16:9
    region: str | None = None            # manual active area "x,y,w,h" in capture pixels

    # --- live viewer ---
    viewer_fps: int = 15                 # browser live view; the nanokvm viewer window asks for its own (--fps)
    viewer_width: int = 1920             # 1920 = 1:1 with the capture (sharpest text)
    viewer_quality: int = 90             # JPEG quality of the live view

    # --- HID timing (milliseconds) ---
    key_hold_ms: int = 25
    key_gap_ms: int = 15
    click_hold_ms: int = 60
    post_action_ms: int = 1500           # max wait for the screen to react after an action
    max_text_len: int = 1500

    # --- screen sharing with Claude (one approval per sharing session) ---
    share_minutes: int = 60              # default share length; Claude may ask for 1..480 minutes

    # --- screenshots saved by the nanokvm command (screenshot, watch, diag) ---
    saved_image_minutes: float = 5.0     # deleted automatically after this long (minimum 1)

    # --- local reading and the cloud coder (Muse Glimmer on this laptop, Muse Spark through Meta's API) ---
    glimmer_port: int = 8001             # local llama-server started by tools\run-glimmer.ps1 (127.0.0.1 only)
    glimmer_timeout_s: float = 600.0
    read_reasoning: str = "low"          # screen reading: Meta's paper shows the same accuracy at low
    minimise_reasoning: str = "high"     # trimming text before it leaves: least leakage at high
    minimise_think_tokens: int = 1024    # cap on Glimmer's thinking while trimming
    outbound: str = "auto"               # text for Muse Spark: auto (send after redaction), approve, local_only
    meta_billing: str = "free_credits"   # free_credits: only stand-in content may go to Meta; paid: real content
    spark_model: str = "muse-spark-1.3"  # a Standard model ID; -contributor IDs are refused
    spark_reasoning: str = "medium"      # Muse Spark reasoning_effort: minimal, low, medium, high, xhigh, max
    spark_max_tokens: int = 8000         # max_completion_tokens, which includes Spark's reasoning
    outbound_max_chars: int = 20000      # refuse to send more text than this in one request
    meta_budget_usd: float = 25.0        # stop sending once the local audit log adds up to this much

    # --- testing ---
    simulate: bool = False               # synthetic screen + fake keyboard/mouse (no hardware)

    home: Path = field(default=_DEFAULT_HOME)

    # ------------------------------------------------------------------
    @property
    def run_dir(self) -> Path:
        return self.home / ".run"

    @property
    def video_id_list(self) -> list[tuple[int, int]]:
        return _parse_ids(self.video_ids)

    @property
    def serial_id_list(self) -> list[tuple[int, int]]:
        return _parse_ids(self.serial_ids)

    @property
    def region_tuple(self) -> tuple[int, int, int, int] | None:
        if not self.region:
            return None
        parts = [int(float(p)) for p in self.region.replace(" ", "").split(",")]
        if len(parts) != 4 or parts[2] <= 0 or parts[3] <= 0:
            raise ValueError(f"region must be 'x,y,w,h' with positive w,h, got {self.region!r}")
        return parts[0], parts[1], parts[2], parts[3]

    @property
    def locked_os_name(self) -> str:
        name = (self.locked_os or "").strip().lower()
        if name not in LOCKED_OSES:
            raise ValueError(f"locked_os must be one of {', '.join(LOCKED_OSES)}, got {self.locked_os!r}")
        return name

    @property
    def layout_warning(self) -> str | None:
        """A Mac's British layout isn't the PC "uk" table: a Mac should use its U.S. input source and "us"."""
        if ((self.locked_os or "").strip().lower() == "macos"
                and (self.keyboard_layout or "").strip().lower() == "uk"):
            return ('keyboard_layout is "uk" (a PC layout) but locked_os is "macos": " @ # \\ | ~ may come '
                    'out wrong. Use the Mac\'s U.S. input source and keyboard_layout = "us" (README section 1a).')
        return None

    @property
    def aspect_value(self) -> float | None:
        if not self.display_aspect:
            return None
        a, _, b = self.display_aspect.partition(":")
        return float(a) / float(b) if b else float(a)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @classmethod
    def load(cls, home: Path | None = None, env: dict[str, str] | None = None) -> "Config":
        env = dict(os.environ if env is None else env)
        home = Path(env.get("NANOKVM_HOME") or home or _DEFAULT_HOME)
        cfg = cls(home=home)
        toml_path = home / "nanokvm.toml"
        values: dict[str, object] = {}
        if toml_path.is_file():
            with open(toml_path, "rb") as fh:
                values.update(tomllib.load(fh))
        for f in fields(cls):
            key = "NANOKVM_" + f.name.upper()
            if key in env and env[key] != "":
                values[f.name] = env[key]
        for f in fields(cls):
            if f.name == "home" or f.name not in values:
                continue
            setattr(cfg, f.name, _coerce(f.name, values[f.name], getattr(cfg, f.name)))
        return cfg


def _coerce(name: str, value: object, default: object) -> object:
    if value is None:
        return None
    if isinstance(default, bool):
        return value if isinstance(value, bool) else _parse_bool(str(value))
    if isinstance(default, int) and not isinstance(default, bool):
        return int(value)  # type: ignore[arg-type]
    if isinstance(default, float):
        return float(value)  # type: ignore[arg-type]
    if name == "video_index":
        return None if str(value).strip() == "" else int(value)  # type: ignore[arg-type]
    return str(value)
