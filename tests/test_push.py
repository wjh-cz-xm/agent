"""push.py 测试：Atmosphere 帧解析、事件判定、hint 提取、URL 构造（纯函数，离线）"""

from xmu_rollcall.push import (
    build_push_url,
    extract_rollcall_hint,
    is_rollcall_event,
    parse_push_frame,
)


# ---------- build_push_url ----------

def test_build_push_url_exact():
    url = build_push_url(12345, "sess-abc")
    assert url == (
        "wss://lnt.xmu.edu.cn/ntf/pubsub/12345"
        "?X-Atmosphere-tracking-id=0"
        "&X-Atmosphere-Transport=websocket"
        "&Content-Type=application%2Fjson"
        "&X-atmo-protocol=true"
        "&X-SESSION-ID=sess-abc"
    )


# ---------- parse_push_frame ----------

def test_parse_valid_rollcall_frame():
    payload = '{"type": "NUMBER_ROLLCALL", "rollcall_id": 42}'
    assert parse_push_frame(f"a|b|c|{payload}") == {
        "type": "NUMBER_ROLLCALL",
        "rollcall_id": 42,
    }


def test_parse_heartbeat_frames():
    assert parse_push_frame("X") is None
    assert parse_push_frame("|X|") is None
    assert parse_push_frame("a|b|c|") is None       # 第 4 段为空
    assert parse_push_frame("a|b|c") is None        # 不足 4 段


def test_parse_bad_json_fourth_part():
    assert parse_push_frame("a|b|c|not-json") is None
    assert parse_push_frame("a|b|c|[1,2,3]") is None  # JSON 但不是 dict


def test_parse_non_string_input():
    assert parse_push_frame(None) is None
    assert parse_push_frame(123) is None


# ---------- is_rollcall_event ----------

def test_is_rollcall_event_case_insensitive():
    assert is_rollcall_event({"type": "NUMBER_ROLLCALL"}) is True
    assert is_rollcall_event({"type": "rollcall_start"}) is True
    assert is_rollcall_event({"type": "RADAR_Rollcall"}) is True


def test_is_rollcall_event_negative():
    assert is_rollcall_event({"type": "notification"}) is False
    assert is_rollcall_event({}) is False
    assert is_rollcall_event({"type": None}) is False


# ---------- extract_rollcall_hint ----------

def test_hint_top_level_rollcall_id():
    hint = extract_rollcall_hint({
        "type": "NUMBER_ROLLCALL",
        "rollcall_id": 10872,
        "course_title": "高等数学",
        "created_by_name": "张老师",
        "department_name": "数学学院",
    })
    assert hint["rollcall_id"] == 10872
    assert hint["course_title"] == "高等数学"


def test_hint_nested_data_id():
    hint = extract_rollcall_hint({
        "type": "ROLLCALL",
        "data": {"id": 99, "course_title": "化学"},
    })
    assert hint["rollcall_id"] == 99
    assert hint["course_title"] == "化学"


def test_hint_nested_list():
    hint = extract_rollcall_hint({
        "type": "ROLLCALL",
        "content": {"rollcalls": [{"id": 7, "course_title": "物理"}]},
    })
    assert hint["rollcall_id"] == 7


def test_hint_ignores_top_level_id():
    """顶层 id 可能是通知自身 id，不采信"""
    hint = extract_rollcall_hint({"type": "ROLLCALL", "id": 999})
    assert hint == {}


def test_hint_garbage():
    assert extract_rollcall_hint({}) == {}
    assert extract_rollcall_hint(None) == {}
    assert extract_rollcall_hint({"type": 123}) == {}
