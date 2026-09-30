"""core.py 测试：兜底轮询间隔加载、签到执行、签到注册/刷新（monkeypatch 离线）"""

import asyncio
import json

import pytest

import xmu_rollcall.core as core
from xmu_rollcall.state import AppState

from conftest import FakeResponse, FakeSession


def _rc(rid=1, kind="number", status="absent", expired=False):
    return {
        "course_title": "高等数学",
        "created_by_name": "张老师",
        "department_name": "数学学院",
        "is_expired": expired,
        "is_number": kind == "number" or kind == "radar",
        "is_radar": kind == "radar",
        "rollcall_id": rid,
        "rollcall_status": "active",
        "scored": False,
        "status": status,
    }


def _state():
    state = AppState(account_name="t", session=None)
    return state


# ---------- get_poll_interval（config 层加载，core 直接引用） ----------

def test_poll_interval_default(monkeypatch, tmp_path):
    import xmu_rollcall.config as c
    monkeypatch.setattr(c, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(c, "CONFIG_FILE", tmp_path / "nope.json")
    assert c.get_poll_interval() == 15


def test_poll_interval_from_config(monkeypatch, tmp_path):
    import xmu_rollcall.config as c
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"interval": 30}), encoding="utf-8")
    monkeypatch.setattr(c, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(c, "CONFIG_FILE", cfg)
    assert c.get_poll_interval() == 30


def test_poll_interval_invalid_falls_back(monkeypatch, tmp_path):
    import xmu_rollcall.config as c
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"interval": "abc"}), encoding="utf-8")
    monkeypatch.setattr(c, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(c, "CONFIG_FILE", cfg)
    assert c.get_poll_interval() == 15


# ---------- _answer_rollcall ----------

def test_answer_number_with_fetched_code(monkeypatch):
    state = _state()
    state.register_card(_rc(1), "number", "数字码签到")
    state.update_card("1", code_status="fetched", code="1234")

    async def fake_answer(session, rid, known_code=None):
        assert known_code == "1234"
        return "1234"

    monkeypatch.setattr(core, "answer_number_code_async", fake_answer)
    monkeypatch.setattr(core, "log_rollcall", lambda *a, **k: None)
    asyncio.run(core._answer_rollcall(state, {"rollcall_id": "1"}))
    card = state.get_card("1")
    assert card["status"] == "success"
    assert card["result"]["code"] == "1234"
    assert state.history[0]["status"] == "success"


def test_answer_number_code_failed_goes_brute_force(monkeypatch):
    state = _state()
    state.register_card(_rc(3), "number", "数字码签到")
    state.update_card("3", code_status="failed", code=None)

    async def fake_answer(session, rid, known_code=None):
        assert known_code is None  # 取码失败 → 不传已知码，暴力枚举
        return False

    monkeypatch.setattr(core, "answer_number_code_async", fake_answer)
    monkeypatch.setattr(core, "log_rollcall", lambda *a, **k: None)
    asyncio.run(core._answer_rollcall(state, {"rollcall_id": "3"}))
    card = state.get_card("3")
    assert card["status"] == "failure"
    assert card["result"]["ok"] is False


def test_answer_radar(monkeypatch):
    state = _state()
    state.register_card(_rc(2, kind="radar"), "radar", "雷达签到")
    monkeypatch.setattr(core, "send_radar", lambda s, rid: True)
    monkeypatch.setattr(core, "log_rollcall", lambda *a, **k: None)
    asyncio.run(core._answer_rollcall(state, {"rollcall_id": "2"}))
    assert state.get_card("2")["status"] == "success"


def test_answer_double_click_guard(monkeypatch):
    state = _state()
    state.register_card(_rc(1), "number", "数字码签到")
    state.update_card("1", status="answering")  # 已进入执行态
    called = []

    async def fake_answer(**kw):
        called.append(1)
        return "x"

    monkeypatch.setattr(core, "answer_number_code_async", fake_answer)
    asyncio.run(core._answer_rollcall(state, {"rollcall_id": "1"}))
    assert called == []


def test_answer_qr_ignored_by_manual_button(monkeypatch):
    state = _state()
    state.register_card(_rc(4, kind="qr"), "qr", "二维码签到")
    monkeypatch.setattr(core, "log_rollcall", lambda *a, **k: None)
    asyncio.run(core._answer_rollcall(state, {"rollcall_id": "4"}))
    assert state.get_card("4")["status"] == "pending"  # qr 只走自动流程


# ---------- _fetch_code_task ----------

def test_fetch_code_success(monkeypatch):
    state = _state()
    state.register_card(_rc(1), "number", "数字码签到")
    monkeypatch.setattr(core, "get_number_code", lambda s, rid: "1234")
    asyncio.run(core._fetch_code_task(state, "1"))
    card = state.get_card("1")
    assert card["code_status"] == "fetched"
    assert card["code"] == "1234"


def test_fetch_code_failure(monkeypatch):
    state = _state()
    state.register_card(_rc(1), "number", "数字码签到")
    monkeypatch.setattr(core, "get_number_code", lambda s, rid: None)
    monkeypatch.setattr(core, "get_signed_count", lambda s, rid: (0, None))
    asyncio.run(core._fetch_code_task(state, "1"))
    assert state.get_card("1")["code_status"] == "failed"


def test_fetch_code_aborts_when_card_gone(monkeypatch):
    state = _state()
    state.register_card(_rc(1), "number", "数字码签到")
    state.dismiss_card("1")
    called = []
    monkeypatch.setattr(core, "get_number_code", lambda s, rid: called.append(1) or "1")
    asyncio.run(core._fetch_code_task(state, "1"))
    assert called == []


# ---------- check_now ----------

def test_check_now_registers_new_cards(monkeypatch):
    state = _state()
    state.session = FakeSession(get_resp=FakeResponse(200, {
        "rollcalls": [
            _rc(1, kind="number"),
            _rc(2, kind="radar"),
            _rc(3, kind="qr"),
        ]
    }))
    monkeypatch.setattr(core, "get_number_code", lambda s, rid: None)
    asyncio.run(core.check_now(state))
    assert state.get_card("1")["kind"] == "number"
    assert state.get_card("2")["kind"] == "radar"
    assert state.get_card("3")["kind"] == "qr"
    assert state.qr_queue.qsize() == 1  # qr 入队自动处理
    assert state.query_count == 1


def test_check_now_skips_already_signed_and_expired_new(monkeypatch):
    state = _state()
    state.session = FakeSession(get_resp=FakeResponse(200, {
        "rollcalls": [
            _rc(1, status="on_call_fine"),  # 已签过（本次拉取才看到）
            _rc(2, expired=True),           # 已过期
        ]
    }))
    asyncio.run(core.check_now(state))
    assert state.get_card("1") is None
    assert state.get_card("2") is None


def test_check_now_refresh_marks_already(monkeypatch):
    state = _state()
    state.session = FakeSession(get_resp=FakeResponse(200, {
        "rollcalls": [_rc(1, status="on_call_fine")]
    }))
    state.register_card(_rc(1), "number", "数字码签到")
    monkeypatch.setattr(core, "log_rollcall", lambda *a, **k: None)
    asyncio.run(core.check_now(state))
    card = state.get_card("1")
    assert card["status"] == "already"
    assert state.history[0]["type"] == "已签到"


def test_check_now_refresh_marks_expired(monkeypatch):
    state = _state()
    state.session = FakeSession(get_resp=FakeResponse(200, {
        "rollcalls": [_rc(1, expired=True)]
    }))
    state.register_card(_rc(1), "number", "数字码签到")
    monkeypatch.setattr(core, "log_rollcall", lambda *a, **k: None)
    asyncio.run(core.check_now(state))
    assert state.get_card("1")["status"] == "expired"


def test_check_now_network_error_does_not_crash(monkeypatch):
    state = _state()
    state.session = FakeSession(get_resp=ConnectionError("boom"))
    asyncio.run(core.check_now(state))  # 不抛异常即通过
    assert state.cards == {}
