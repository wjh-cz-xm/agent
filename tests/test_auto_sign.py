"""过半自动签到安全开关：默认关闭、绝对时间到期、原子持久化。"""

import json

from xmu_rollcall.auto_sign import disable, enable, get_status


def test_default_is_disabled(tmp_path):
    status = get_status(tmp_path / "missing.json", current=1000)
    assert status["enabled"] is False
    assert status["expires_at"] is None


def test_enable_expires_by_wall_clock_across_reads(tmp_path):
    path = tmp_path / "auto_sign.json"
    enabled = enable(path, current=1000, ttl_seconds=12 * 60 * 60)
    assert enabled["enabled"] is True
    assert enabled["expires_at"] == 44200
    assert get_status(path, current=44199)["enabled"] is True
    expired = get_status(path, current=44200)
    assert expired["enabled"] is False
    assert expired["remaining_seconds"] == 0


def test_reenable_resets_twelve_hours_and_disable_is_immediate(tmp_path):
    path = tmp_path / "auto_sign.json"
    enable(path, current=1000)
    renewed = enable(path, current=2000)
    assert renewed["expires_at"] == 2000 + 12 * 60 * 60
    assert disable(path, current=3000)["enabled"] is False
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["enabled"] is False
    assert saved["expires_at"] == 0
