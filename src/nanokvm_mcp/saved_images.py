"""Screenshots saved by the ``nanokvm`` command are deleted automatically after a few minutes.

Each saved file is listed in .run/saved_images.json with the time it expires. The service deletes
expired files every 15 s and deletes all of them when it stops; every ``nanokvm`` command also sweeps
when it starts, so the files go even when the service isn't running. Image files in the folders the
command writes to (.run and .run/watch) are also deleted by age if the list misses them.

Only image files are ever deleted, whatever the list says. Deleting doesn't erase the data from an SSD;
the point is that screenshots of the locked computer don't pile up where other programs and AI file tools
can read them.
"""
from __future__ import annotations

import json
import logging
import os
import time
from contextlib import contextmanager
from pathlib import Path

log = logging.getLogger(__name__)

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp"}
_LIST = "saved_images.json"
_LOCK = "saved_images.lock"
_SCAN_DIRS = ("", "watch")    # folders under .run that the nanokvm command saves into


def _minutes(keep_minutes: float) -> float:
    return max(1.0, float(keep_minutes))


@contextmanager
def _locked(run_dir: Path, timeout: float = 3.0):
    """Cross-process lock so the service and a command don't rewrite the list at the same time."""
    lock = run_dir / _LOCK
    deadline = time.monotonic() + timeout
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            if time.monotonic() > deadline:   # left behind by a crashed process: take it over
                try:
                    lock.unlink()
                except OSError:
                    pass
                deadline = time.monotonic() + timeout
            time.sleep(0.02)
    try:
        yield
    finally:
        os.close(fd)
        try:
            lock.unlink()
        except OSError:
            pass


def _load(run_dir: Path) -> dict[str, float]:
    try:
        data = json.loads((run_dir / _LIST).read_text(encoding="utf-8"))
        return {str(k): float(v) for k, v in data.items()}
    except (OSError, ValueError, AttributeError, TypeError):
        return {}


def _store(run_dir: Path, entries: dict[str, float]) -> None:
    tmp = run_dir / (_LIST + ".tmp")
    tmp.write_text(json.dumps(entries, indent=1), encoding="utf-8")
    os.replace(tmp, run_dir / _LIST)


def _delete(path: Path) -> bool:
    if path.suffix.lower() not in IMAGE_SUFFIXES:
        return False
    try:
        path.unlink()
        return True
    except OSError as e:
        log.warning("could not delete %s: %s", path, e)
        return False


def remember(run_dir: Path, path: Path, keep_minutes: float) -> None:
    """List ``path`` for deletion ``keep_minutes`` from now; saving it again restarts the clock."""
    run_dir.mkdir(parents=True, exist_ok=True)
    with _locked(run_dir):
        entries = _load(run_dir)
        entries[str(Path(path).resolve())] = time.time() + _minutes(keep_minutes) * 60
        _store(run_dir, entries)


def sweep(run_dir: Path, keep_minutes: float, everything: bool = False, now: float | None = None) -> list[Path]:
    """Delete listed screenshots whose time is up (all of them with ``everything``), and unlisted image
    files in .run and .run/watch older than ``keep_minutes``. Returns the deleted paths."""
    if not run_dir.is_dir():
        return []
    now = time.time() if now is None else now
    deleted: list[Path] = []
    with _locked(run_dir):
        entries = _load(run_dir)
        kept: dict[str, float] = {}
        for name, expires in entries.items():
            path = Path(name)
            if path.suffix.lower() not in IMAGE_SUFFIXES or not path.is_file():
                continue                      # gone already, or never an image: drop it from the list
            if (everything or expires <= now) and _delete(path):
                deleted.append(path)
                continue
            kept[name] = expires
        cutoff = now - _minutes(keep_minutes) * 60
        for sub in _SCAN_DIRS:
            folder = run_dir / sub
            if not folder.is_dir():
                continue
            for path in folder.iterdir():
                if not path.is_file() or str(path.resolve()) in kept:
                    continue
                try:
                    old = everything or path.stat().st_mtime <= cutoff
                except OSError:
                    continue
                if old and _delete(path):
                    deleted.append(path)
        if kept != entries:
            _store(run_dir, kept)
    for path in deleted:
        log.info("deleted saved screenshot %s", path)
    return deleted
