"""rollcall_handler.py 测试：签到数据提取与类型判定（纯逻辑）"""

import xmu_rollcall.rollcall_handler as rh


def _rollcall(rid=1, is_number=True, is_radar=False, status="absent"):
    return {
        "course_title": "高等数学",
        "created_by_name": "张老师",
        "department_name": "数学学院",
        "is_expired": False,
        "is_number": is_number,
        "is_radar": is_radar,
        "rollcall_id": rid,
        "rollcall_status": "active",
        "scored": False,
        "status": status,
    }


def test_extract_rollcalls_shape():
    count, rollcalls = rh.extract_rollcalls({"rollcalls": [_rollcall()]})
    assert count == 1
    assert rollcalls[0]["course_title"] == "高等数学"
    assert rollcalls[0]["rollcall_id"] == 1


def test_extract_rollcalls_empty():
    count, rollcalls = rh.extract_rollcalls({"rollcalls": []})
    assert count == 0
    assert rollcalls == []


def test_classify_number():
    assert rh.classify(_rollcall(is_number=True, is_radar=False)) == "number"


def test_classify_radar():
    # 雷达签到可能同时带 is_number 标记，is_radar 优先
    assert rh.classify(_rollcall(is_number=True, is_radar=True)) == "radar"


def test_classify_qr():
    assert rh.classify(_rollcall(is_number=False, is_radar=False)) == "qr"


def test_type_label():
    assert rh.type_label("number") == "数字码签到"
    assert rh.type_label("radar") == "雷达签到"
    assert rh.type_label("qr") == "二维码签到"
    assert rh.type_label("unknown") == "签到"


def test_self_status():
    assert rh.self_status(_rollcall(status="absent")) == "absent"
    assert rh.self_status(_rollcall(status="on_call_fine")) == "on_call_fine"
