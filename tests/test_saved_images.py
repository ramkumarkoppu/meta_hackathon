"""Screenshots saved by the nanokvm command are deleted automatically (saved_images.py)."""
import base64
import json
import time

from nanokvm_mcp import cli, saved_images
from nanokvm_mcp.config import Config


def _img(path, age_s=0.0):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xff\xd8 fake jpeg")
    if age_s:
        t = time.time() - age_s
        import os
        os.utime(path, (t, t))
    return path


def test_listed_screenshot_is_deleted_only_when_its_time_is_up(tmp_path):
    run = tmp_path / ".run"
    shot = _img(run / "screenshot.jpg")
    saved_images.remember(run, shot, keep_minutes=5)
    assert saved_images.sweep(run, 5, now=time.time() + 4 * 60) == []
    assert shot.exists()
    assert saved_images.sweep(run, 5, now=time.time() + 6 * 60) == [shot]
    assert not shot.exists()
    assert json.loads((run / "saved_images.json").read_text()) == {}


def test_saving_again_restarts_the_clock(tmp_path):
    run = tmp_path / ".run"
    shot = _img(run / "watch" / "latest.jpg")
    saved_images.remember(run, shot, keep_minutes=5)
    first = json.loads((run / "saved_images.json").read_text())[str(shot.resolve())]
    time.sleep(0.05)
    saved_images.remember(run, shot, keep_minutes=5)
    assert json.loads((run / "saved_images.json").read_text())[str(shot.resolve())] > first


def test_everything_deletes_all_listed_and_unlisted_screenshots(tmp_path):
    run = tmp_path / ".run"
    elsewhere = _img(tmp_path / "Desktop" / "mine.jpg")       # saved with 'nanokvm screenshot FILE'
    saved_images.remember(run, elsewhere, keep_minutes=60)
    stray = _img(run / "watch" / "change-1.jpg")              # fresh, not on the list
    deleted = saved_images.sweep(run, 60, everything=True)
    assert set(deleted) == {elsewhere, stray}


def test_unlisted_images_go_by_age_and_other_files_are_never_touched(tmp_path):
    run = tmp_path / ".run"
    old = _img(run / "diag.jpg", age_s=10 * 60)
    new = _img(run / "watch" / "latest.jpg")
    for name in ("token", "service.log", "calibration.json", "llama_api_key"):
        (run / name).write_text("keep me")
    (run / "ab").mkdir()
    ab = _img(run / "ab" / "ring_buffer.png", age_s=10 * 60)  # A/B frames live in a subfolder: not scanned
    assert saved_images.sweep(run, 5) == [old]
    assert new.exists() and ab.exists()
    assert all((run / n).read_text() == "keep me" for n in ("token", "service.log", "calibration.json", "llama_api_key"))


def test_a_damaged_list_cannot_delete_other_files(tmp_path):
    run = tmp_path / ".run"
    run.mkdir()
    precious = tmp_path / "work.cpp"
    precious.write_text("int main() {}")
    (run / "saved_images.json").write_text(json.dumps({str(precious): 0}))
    assert saved_images.sweep(run, 5, everything=True) == []
    assert precious.exists()
    assert json.loads((run / "saved_images.json").read_text()) == {}


def test_minimum_keep_time_is_one_minute(tmp_path):
    run = tmp_path / ".run"
    shot = _img(run / "screenshot.jpg")
    saved_images.remember(run, shot, keep_minutes=0)
    assert saved_images.sweep(run, 0, now=time.time() + 30) == []
    assert shot.exists()


def test_cli_registers_every_saved_screenshot(tmp_path):
    cfg = Config(home=tmp_path, saved_image_minutes=2)
    shot = {"jpeg_b64": base64.b64encode(b"\xff\xd8 fake").decode()}
    path = cli._save_jpeg(shot, cfg.run_dir / "screenshot.jpg", cfg)
    listed = json.loads((cfg.run_dir / "saved_images.json").read_text())
    assert str(path.resolve()) in listed
    assert 60 < listed[str(path.resolve())] - time.time() <= 120
