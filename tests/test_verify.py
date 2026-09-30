"""verify.py 测试：直接取码、签到人数统计、单码提交（全部离线假件）"""

import asyncio
import uuid

import pytest

from xmu_rollcall.verify import (
    _parse_number_code,
    get_number_code,
    _send_single_code,
    _find_student_rollcalls,
    get_signed_count,
    pad,
    _aiohttp_session_from,
    answer_number_code_async,
    send_code,
    send_radar,
)

from conftest import FakeResponse


class FakeAioResp:
    """伪造 aiohttp 响应：只有 status"""

    def __init__(self, status):
        self.status = status


class FakeAioClient:
    """伪造 aiohttp.ClientSession 的最小面：记录 put 调用，按序弹响应"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.put_calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def put(self, url, json=None):
        self.put_calls.append({"url": url, "json": json})
        resp = self.responses.pop(0) if self.responses else FakeAioResp(500)

        class _Ctx:
            async def __aenter__(self):
                return resp

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


# ---------- _parse_number_code ----------

@pytest.mark.parametrize("data,expected", [
    ({"number_code": 123}, "0123"),
    ({"number_code": "1234"}, "1234"),
    ({"number_code": " 5678 "}, "5678"),
    ({"data": {"number_code": "9999"}}, "9999"),
    ({"rollcalls": [{"number_code": 4321}, {"number_code": None}]}, "4321"),
    ({"student_rollcalls": [{"status": "active"}, {"number_code": 55}]}, "0055"),
    ({"number_code": None}, None),
    ({"number_code": ""}, None),
    ({"number_code": "abcd"}, None),
    ({"number_code": "12345"}, None),  # 超过 4 位拒绝
    ({"status": "active"}, None),
    ([], None),
    ("garbage", None),
])
def test_parse_number_code(data, expected):
    assert _parse_number_code(data) == expected


def test_pad():
    assert pad(7) == "0007"
    assert pad(1234) == "1234"


# ---------- get_number_code ----------

def test_get_number_code_ok(make_session):
    s = make_session(get_resp=FakeResponse(200, {"number_code": 99}))
    assert get_number_code(s, 1) == "0099"
    # 防检测优化：请求头应带 lnt Referer
    assert s.get_calls[0]["headers"].get("Referer") == "https://lnt.xmu.edu.cn/"


def test_get_number_code_non200(make_session):
    s = make_session(get_resp=FakeResponse(403, {}))
    assert get_number_code(s, 1) is None


def test_get_number_code_network_error(make_session):
    s = make_session(get_resp=ConnectionError("boom"))
    assert get_number_code(s, 1) is None


# ---------- _send_single_code ----------

def test_send_single_code_success(make_session):
    s = make_session(put_resp=FakeResponse(200, {}))
    assert _send_single_code(s, 1, "1234") is True
    assert len(s.put_calls) == 1
    payload = s.put_calls[0]["json"]
    assert payload["numberCode"] == "1234"
    uuid.UUID(payload["deviceId"])  # 合法 UUID


def test_send_single_code_retry_then_success(make_session):
    s = make_session(put_resp=[FakeResponse(500, {}), FakeResponse(200, {})])
    assert _send_single_code(s, 1, "1234") is True
    assert len(s.put_calls) == 2


def test_send_single_code_all_fail(make_session):
    s = make_session(put_resp=FakeResponse(500, {}))
    assert _send_single_code(s, 1, "1234") is False
    assert len(s.put_calls) == 3  # 3 次重试后放弃


# ---------- 签到人数统计 ----------

def test_find_student_rollcalls_nested():
    data = {"data": {"rollcall": {"student_rollcalls": [{"rollcall_status": "on_call_fine"}]}}}
    assert _find_student_rollcalls(data) == [{"rollcall_status": "on_call_fine"}]


def test_find_student_rollcalls_missing():
    assert _find_student_rollcalls({"a": 1}) == []


def test_get_signed_count(make_session):
    data = {
        "student_rollcalls": [
            {"rollcall_status": "on_call_fine"},
            {"rollcall_status": "ON_CALL_FINE"},  # 大小写不敏感
            {"rollcall_status": "absent"},
            {},
        ]
    }
    s = make_session(get_resp=FakeResponse(200, data))
    count, _ = get_signed_count(s, 1)
    assert count == 2


def test_get_signed_count_error(make_session):
    s = make_session(get_resp=FakeResponse(403, {}))
    count, _ = get_signed_count(s, 1)
    assert count is None


# ---------- _aiohttp_session_from ----------

def test_aiohttp_session_from_copies_headers_and_cookies():
    import requests
    from yarl import URL

    s = requests.Session()
    s.headers["X-SESSION-ID"] = "abc123"
    s.cookies.set("sid", "cookie-value", domain="lnt.xmu.edu.cn", path="/")

    async def _check():
        client = _aiohttp_session_from(s)
        try:
            assert client.headers.get("X-SESSION-ID") == "abc123"
            jar = client.cookie_jar.filter_cookies(URL("http://lnt.xmu.edu.cn/"))
            assert jar.get("sid") is not None
            assert jar["sid"].value == "cookie-value"
        finally:
            await client.close()

    asyncio.run(_check())


# ---------- answer_number_code_async（异步入口） ----------

def test_answer_number_code_async_known_code_success():
    client = FakeAioClient([FakeAioResp(200)])
    result = asyncio.run(
        answer_number_code_async(None, 42, known_code="1234",
                                 client_factory=lambda s: client)
    )
    assert result == "1234"
    assert len(client.put_calls) == 1
    assert client.put_calls[0]["json"]["numberCode"] == "1234"
    uuid.UUID(client.put_calls[0]["json"]["deviceId"])
    assert "answer_number_rollcall" in client.put_calls[0]["url"]


def test_answer_number_code_async_known_code_failure_falls_back():
    """known_code 提交失败 → 落回暴力枚举；枚举未命中 → False。

    用假 client 承接 10000 个枚举任务（全部 500），验证回退语义与
    任务清理（不抛异常即通过）。
    """
    client = FakeAioClient([FakeAioResp(500)] * 20000)
    result = asyncio.run(
        answer_number_code_async(None, 42, known_code="0000",
                                 client_factory=lambda s: client)
    )
    assert result is False


# ---------- send_code（同步薄壳） ----------

def test_send_code_sync_wrapper_known_code_success(monkeypatch):
    """known_code 成功路径只走 _send_single_code，不创建事件循环"""
    import xmu_rollcall.verify as v

    calls = []
    monkeypatch.setattr(
        v, "_send_single_code",
        lambda s, rid, code, attempts=3: calls.append(code) or True,
    )
    assert v.send_code(None, 1, known_code="1234") == "1234"
    assert calls == ["1234"]


# ---------- send_radar ----------

def test_send_radar_first_guess_hits(make_session):
    s = make_session(put_resp=FakeResponse(200, {}))
    assert send_radar(s, 1) is True
    assert len(s.put_calls) == 1
    assert s.put_calls[0]["timeout"] == 10  # 顺手加上的超时保护


def test_send_radar_second_guess_hits(make_session):
    s = make_session(put_resp=[FakeResponse(400, {"distance": 100}), FakeResponse(200, {})])
    assert send_radar(s, 1) is True
    assert len(s.put_calls) == 2


def test_send_radar_trilateration_hits(make_session):
    # 两个初猜点相距约 38km，distance 需 > 19km 才有圆交点
    s = make_session(put_resp=[
        FakeResponse(400, {"distance": 25000.0}),
        FakeResponse(400, {"distance": 25000.0}),
        FakeResponse(200, {}),
    ])
    assert send_radar(s, 1) is True
    assert len(s.put_calls) == 3
    assert s.put_calls[2]["json"]["latitude"] not in (24.3, 24.6)  # 交点而非初猜


def test_send_radar_network_error_returns_false(make_session):
    s = make_session(put_resp=ConnectionError("boom"))
    assert send_radar(s, 1) is False
