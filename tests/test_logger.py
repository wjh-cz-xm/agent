"""logger.py 测试：签到日志写入、成功过滤、100 条截断"""

import json

import xmu_rollcall.logger as logger_module


def test_log_roundtrip(monkeypatch, tmp_path):
    log_file = tmp_path / "rollcall_log.json"
    monkeypatch.setattr(logger_module, "LOG_FILE", log_file)
    logger_module.log_rollcall("化学", "数字码签到", "success", code="1234")
    logger_module.log_rollcall("化学", "数字码签到", "failure", "签到失败")

    recent = logger_module.get_recent_successful(2)
    assert len(recent) == 1  # 只返回 success 记录
    assert recent[0]["course"] == "化学"
    assert recent[0]["code"] == "1234"


def test_log_trim_to_max_entries(monkeypatch, tmp_path):
    log_file = tmp_path / "rollcall_log.json"
    monkeypatch.setattr(logger_module, "LOG_FILE", log_file)
    for i in range(105):
        logger_module.log_rollcall(f"课{i}", "数字码签到", "success")

    logs = json.loads(log_file.read_text(encoding="utf-8"))
    assert len(logs) == logger_module.MAX_LOG_ENTRIES
    assert logs[-1]["course"] == "课104"


def test_recent_without_file(monkeypatch, tmp_path):
    monkeypatch.setattr(logger_module, "LOG_FILE", tmp_path / "none.json")
    assert logger_module.get_recent_successful() == []
