"""ui.py 测试：Flask test_client + 假 state/队列（全离线）"""

import queue

import pytest

import xmu_rollcall.ui as ui_mod
from xmu_rollcall.state import AppState


@pytest.fixture
def bound_ui(monkeypatch):
    monkeypatch.setattr(
        ui_mod, "get_auto_sign_status",
        lambda: {"enabled": False, "remaining_seconds": 0},
    )
    state = AppState(account_name="张三")
    q = queue.Queue()
    ui_mod.bind(state, q)
    return state, q, ui_mod.app.test_client()


def test_dashboard_page(bound_ui):
    _, _, client = bound_ui
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "xmu-rollcall" in html
    assert 'id="cards"' in html
    assert "过半自动签到" in html


def test_api_state(bound_ui):
    state, _, client = bound_ui
    state.register_card(
        {"rollcall_id": 1, "course_title": "高等数学"}, "number", "数字码签到"
    )
    resp = client.get("/api/state")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["system"]["account_name"] == "张三"
    assert data["system"]["auto_sign"]["enabled"] is False
    assert len(data["cards"]) == 1
    assert data["cards"][0]["course_title"] == "高等数学"


def test_api_answer_enqueues(bound_ui):
    state, q, client = bound_ui
    state.register_card(
        {"rollcall_id": 1, "course_title": "高等数学"}, "number", "数字码签到"
    )
    resp = client.post("/api/answer", json={"rollcall_id": 1})
    assert resp.status_code == 200
    req = q.get_nowait()
    assert req["rollcall_id"] == "1"


def test_api_answer_non_pending_409(bound_ui):
    state, q, client = bound_ui
    state.register_card({"rollcall_id": 1}, "number", "数字码签到")
    state.update_card("1", status="answering")
    resp = client.post("/api/answer", json={"rollcall_id": 1})
    assert resp.status_code == 409
    assert q.empty()


def test_api_answer_unknown_404(bound_ui):
    _, _, client = bound_ui
    assert client.post("/api/answer", json={"rollcall_id": 999}).status_code == 404


def test_api_dismiss(bound_ui):
    state, _, client = bound_ui
    state.register_card({"rollcall_id": 1}, "radar", "雷达签到")
    state.mark_terminal("1", "failure", False, detail="签到失败")
    resp = client.post("/api/dismiss", json={"rollcall_id": 1})
    assert resp.status_code == 200
    assert state.get_card("1") is None


def test_api_auto_sign_toggle(bound_ui, monkeypatch):
    _, _, client = bound_ui
    monkeypatch.setattr(
        ui_mod, "enable_auto_sign",
        lambda: {"enabled": True, "remaining_seconds": 43200},
    )
    monkeypatch.setattr(
        ui_mod, "disable_auto_sign",
        lambda: {"enabled": False, "remaining_seconds": 0},
    )
    assert client.post("/api/auto-sign", json={"enabled": True}).get_json()["auto_sign"]["enabled"]
    assert not client.post("/api/auto-sign", json={"enabled": False}).get_json()["auto_sign"]["enabled"]
    assert client.post("/api/auto-sign", json={"enabled": "yes"}).status_code == 400


def test_api_not_ready_returns_503():
    ui_mod.bind(None, None)
    client = ui_mod.app.test_client()
    assert client.get("/api/state").status_code == 503
    assert client.post("/api/answer", json={"rollcall_id": 1}).status_code == 503
