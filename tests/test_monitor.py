"""Offline cloud-monitor behavior: read-only calls, state, recovery, and secrets."""

import asyncio
import json

import pytest
import requests
from click.testing import CliRunner

import xmu_rollcall.monitor as monitor
from conftest import FakeResponse, FakeSession


def rollcall(rid=12, status="absent", **fields):
    return {
        "rollcall_id": rid, "course_title": "示例课程", "created_by_name": "示例教师",
        "is_number": True, "is_radar": False, "is_expired": False,
        "status": status, "rollcall_status": "active", **fields,
    }


def test_restart_deduplicates_and_tracks_self_status(tmp_path):
    events = []
    store = monitor.MonitorStore(tmp_path, events.append)
    data = {"rollcalls": [rollcall()]}
    store.update(data)
    store.update(data)
    assert [e["event"] for e in events] == ["new_rollcall"]
    resumed = monitor.MonitorStore(tmp_path, events.append)
    resumed.update(data)
    assert len(events) == 1
    resumed.update({"rollcalls": [rollcall(status="on_call_fine")]})
    assert events[-1]["event"] == "rollcall_changed"
    resumed.update({"rollcalls": []})
    assert events[-1]["event"] == "rollcall_removed"


def test_only_new_pending_rollcalls_alert_and_payload_is_filtered(tmp_path):
    events = []
    store = monitor.MonitorStore(tmp_path, events.append)
    store.update({"rollcalls": [
        rollcall(1, status="on_call_fine"), rollcall(2, is_expired=True),
        rollcall(3, number_code="secret-code", cookies="secret-cookie"),
    ]})
    assert [e["rollcall"]["rollcall_id"] for e in events] == ["3"]
    assert "secret-" not in store.path.read_text(encoding="utf-8")
    assert "secret-" not in json.dumps(events)


@pytest.mark.parametrize("data", [{}, {"rollcalls": None}, {"rollcalls": [None]}, {"rollcalls": [{}]}])
def test_invalid_response_does_not_erase_last_good_state(tmp_path, data):
    store = monitor.MonitorStore(tmp_path, lambda e: None)
    store.update({"rollcalls": [rollcall()]})
    with pytest.raises(monitor.MonitorError):
        store.update(data)
    assert "12" in store.records


def test_no_repeated_error_spam_and_recovery_is_recorded(tmp_path):
    events = []
    store = monitor.MonitorStore(tmp_path, events.append)
    store.fail("request_timeout")
    store.fail("request_timeout")
    store.update({"rollcalls": []})
    assert [e["event"] for e in events] == ["monitor_error", "monitor_recovered"]
    assert store.status["healthy"] is True


def test_authentication_and_monitor_only_read_profile_and_rollcalls(tmp_path, monkeypatch):
    session = FakeSession(get_resp=[
        FakeResponse(json_data={"id": 4, "name": "sample"}, headers={"X-SESSION-ID": "sample-session"}),
        FakeResponse(json_data={"rollcalls": [rollcall()]}),
    ])
    session.close = lambda: None
    monkeypatch.setattr(monitor, "_login_tronclass", lambda *a, **kw: session)
    store = monitor.MonitorStore(tmp_path, lambda e: None)
    watcher = monitor.Monitor(store, "fake-user", "fake-password", use_push=False)
    assert asyncio.run(watcher.run(once=True)) is True
    assert [call["url"] for call in session.get_calls] == [
        "https://lnt.xmu.edu.cn/api/profile",
        "https://lnt.xmu.edu.cn/api/radar/rollcalls",
    ]
    assert all(call["timeout"] == (10, 25) for call in session.get_calls)
    assert session.put_calls == []
    state_text = store.path.read_text(encoding="utf-8")
    assert "fake-password" not in state_text
    assert "sample-session" not in state_text


def test_expired_session_is_discarded_before_next_login(tmp_path, monkeypatch):
    sessions = []
    closed = []
    for code in (401, 200):
        session = FakeSession(get_resp=FakeResponse(code, {"rollcalls": []}))
        session.close = lambda s=session: closed.append(s)
        sessions.append(session)
    original_sessions = list(sessions)
    monkeypatch.setattr(monitor, "authenticate", lambda *a: (sessions.pop(0), 4, None))
    store = monitor.MonitorStore(tmp_path, lambda e: None)
    watcher = monitor.Monitor(store, "u", "p", use_push=False)
    assert asyncio.run(watcher.run(once=True)) is False
    assert watcher.session is None
    assert original_sessions[0] in closed
    assert asyncio.run(watcher.run(once=True)) is True
    assert original_sessions[1] in closed


def test_network_error_never_logs_exception_credentials(tmp_path, monkeypatch, capsys):
    def failed_login(*args):
        raise requests.ConnectionError("https://example/?token=private-secret")
    monkeypatch.setattr(monitor, "authenticate", failed_login)
    store = monitor.MonitorStore(tmp_path)
    watcher = monitor.Monitor(store, "u", "private-password", use_push=False)
    assert asyncio.run(watcher.run(once=True)) is False
    store.close()
    assert "private-" not in capsys.readouterr().out
    for path in tmp_path.iterdir():
        assert "private-" not in path.read_text(encoding="utf-8")


def test_missing_credentials_fail_before_creating_state(tmp_path, monkeypatch):
    monkeypatch.delenv("XMU_USERNAME", raising=False)
    monkeypatch.delenv("XMU_PASSWORD", raising=False)
    result = CliRunner().invoke(monitor.main, ["--once", "--state-dir", str(tmp_path / "state")])
    assert result.exit_code != 0
    assert "XMU_USERNAME" in result.output
    assert not (tmp_path / "state").exists()


def test_http_session_keeps_proxy_and_applies_timeout_and_ca(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:80")
    monkeypatch.setenv("SSL_CERT_FILE", "/example/cloud-ca.pem")
    monkeypatch.delenv("REQUESTS_CA_BUNDLE", raising=False)
    captured = {}
    def request(self, method, url, **kwargs):
        captured.update(kwargs)
    monkeypatch.setattr(requests.Session, "request", request)
    session = monitor.CloudSession()
    session.get("https://example.invalid")
    assert session.trust_env is True
    assert captured["timeout"] == (10, 25)
    assert captured["verify"] == "/example/cloud-ca.pem"


def test_push_wakes_poll_and_cancellation_closes_client(tmp_path, monkeypatch):
    class Socket:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        def __aiter__(self):
            return self
        async def __anext__(self):
            await asyncio.sleep(3600)

    options = {}
    closed = []
    class Client:
        def __init__(self, **kwargs):
            options.update(kwargs)
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            closed.append(True)
        def ws_connect(self, *args, **kwargs):
            return Socket()

    monkeypatch.setattr(monitor.aiohttp, "ClientSession", Client)
    monkeypatch.setattr(monitor.ssl, "create_default_context", lambda **kw: object())
    store = monitor.MonitorStore(tmp_path, lambda e: None)
    watcher = monitor.Monitor(store, "u", "p")
    watcher.session = requests.Session()
    watcher.user_id, watcher.session_id = 1, "session"

    async def run():
        task = asyncio.create_task(watcher.listen())
        await asyncio.wait_for(watcher.wake.wait(), timeout=2)
        assert store.status["push"] == "connected"
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    asyncio.run(run())
    assert options["trust_env"] is True
    assert closed == [True]


def test_cli_rejects_aggressive_poll_interval():
    result = CliRunner().invoke(monitor.main, ["--interval", "1"])
    assert result.exit_code != 0
