"""state.py 测试：注册去重、终态流转、TTL 清理、快照可序列化"""

import json
import time

from xmu_rollcall.state import AppState


def _rc(rid="1", expired=False):
    return {
        "course_title": "高等数学",
        "created_by_name": "张老师",
        "department_name": "数学学院",
        "is_expired": expired,
        "rollcall_id": rid,
        "status": "absent",
    }


def test_register_card_basic():
    s = AppState(account_name="test")
    assert s.register_card(_rc("1"), "number", "数字码签到") is True
    card = s.get_card("1")
    assert card["kind"] == "number"
    assert card["code_status"] == "fetching"
    assert card["status"] == "pending"
    assert card["signed_count"] is None
    assert card["result"] is None


def test_register_card_other_kind_code_status():
    s = AppState()
    s.register_card(_rc("2"), "radar", "雷达签到")
    assert s.get_card("2")["code_status"] == "-"


def test_register_card_dedupe_while_active():
    s = AppState()
    assert s.register_card(_rc("1"), "number", "数字码签到") is True
    assert s.register_card(_rc("1"), "number", "数字码签到") is False


def test_register_card_rejects_expired():
    s = AppState()
    assert s.register_card(_rc("9", expired=True), "number", "数字码签到") is False
    assert s.get_card("9") is None


def test_register_card_rejects_missing_id():
    s = AppState()
    assert s.register_card({"rollcall_id": None}, "radar", "雷达签到") is False


def test_update_card():
    s = AppState()
    s.register_card(_rc("1"), "number", "数字码签到")
    s.update_card("1", code_status="fetched", code="1234", signed_count=12)
    card = s.get_card("1")
    assert card["code"] == "1234"
    assert card["signed_count"] == 12
    # 未知 id 静默
    s.update_card("999", status="success")


def test_mark_terminal_appends_history():
    s = AppState()
    s.register_card(_rc("1"), "number", "数字码签到")
    s.mark_terminal("1", "success", True, code="1234", detail="签到成功")
    card = s.get_card("1")
    assert card["status"] == "success"
    assert card["result"]["ok"] is True
    assert card["result"]["code"] == "1234"
    assert len(s.history) == 1
    entry = s.history[0]
    assert entry["course"] == "高等数学"
    assert entry["type"] == "数字码签到"
    assert entry["status"] == "success"


def test_mark_terminal_log_type_override():
    s = AppState()
    s.register_card(_rc("1"), "number", "数字码签到")
    s.mark_terminal("1", "already", True, detail="已签到过", log_type="已签到")
    assert s.history[0]["type"] == "已签到"


def test_dismiss_moves_to_handled():
    s = AppState()
    s.register_card(_rc("1"), "number", "数字码签到")
    s.mark_terminal("1", "failure", False, detail="签到失败")
    s.dismiss_card("1")
    assert s.get_card("1") is None
    # 已处理的 id 不可再注册
    assert s.register_card(_rc("1"), "number", "数字码签到") is False


def test_prune_terminal_and_handled_ttl(monkeypatch):
    s = AppState()
    s.register_card(_rc("1"), "number", "数字码签到")
    s.register_card(_rc("2"), "radar", "雷达签到")
    s.mark_terminal("1", "failure", False, detail="签到失败")

    # 终态卡片超龄 → 移入去重表
    old = time.time() - 601
    with s._lock:
        s.cards["1"]["detected_at"] = old
    s.prune()
    assert s.get_card("1") is None
    assert "1" in s.handled_ids
    assert s.get_card("2") is not None  # 活跃卡片不受影响

    # 去重表超龄 → 删除，可重新注册
    with s._lock:
        s.handled_ids["1"] = time.time() - 1201
    s.prune()
    assert "1" not in s.handled_ids
    assert s.register_card(_rc("1"), "number", "数字码签到") is True


def test_snapshot_is_json_serializable_and_sorted():
    s = AppState(account_name="张三")
    s.register_card(_rc("1"), "number", "数字码签到")
    s.register_card(_rc("2"), "radar", "雷达签到")
    snap = s.snapshot()
    json.dumps(snap)  # 不抛即通过
    assert snap["system"]["account_name"] == "张三"
    assert snap["system"]["version"]
    assert [c["rollcall_id"] for c in snap["cards"]] == ["2", "1"]  # 最新在前
    assert snap["history"] == []


def test_double_click_guard_is_core_concern():
    """UI 只入队；是否重复执行由 core 检查卡片 status。这里验证状态可判定。"""
    s = AppState()
    s.register_card(_rc("1"), "number", "数字码签到")
    assert s.get_card("1")["status"] == "pending"
    s.update_card("1", status="answering")
    assert s.get_card("1")["status"] != "pending"
