"""parse_code.py 测试：TronClass 二维码 payload 解码（已知向量）"""

from xmu_rollcall.parse_code import parse_sign_qr_code, to_base36

# 特殊前缀字符：na=chr(26) 布尔、ra=chr(16) 浮点、ea=chr(31)→~、ta=chr(30)→!

def test_basic_fields():
    result = parse_sign_qr_code("!0~42!4~10872!3~xyz!6~1")
    assert result == {
        "courseId": "42",
        "rollcallId": "10872",
        "data": "xyz",
        "accessCode": "1",
    }


def test_boolean_true():
    # enableGroupRollcall=8，值 ia = chr(26)+"1" → True
    assert parse_sign_qr_code("!8~" + chr(26) + "1") == {"enableGroupRollcall": True}


def test_boolean_false():
    assert parse_sign_qr_code("!8~" + chr(26) + "0") == {"enableGroupRollcall": False}


def test_float_field():
    # activityId=1，ra 前缀 + base36 "g.s" → 16.28
    assert parse_sign_qr_code("!1~" + chr(16) + "g.s") == {"activityId": 16.28}


def test_escaping():
    # ea → "~"，ta → "!"
    assert parse_sign_qr_code("!3~a" + chr(31) + "b" + chr(30) + "c") == {"data": "a~b!c"}


def test_ua_value_mapping():
    # sa（ua 反转）表用于「值」的映射：vote 的值 = chr(26) + base36(2+2) = chr(26)+"4"
    assert parse_sign_qr_code("!0~" + chr(26) + "4") == {"courseId": "vote"}


def test_empty_and_garbage():
    assert parse_sign_qr_code("") == {}
    assert parse_sign_qr_code(None) == {}
    assert parse_sign_qr_code("no-separator") == {}
    assert parse_sign_qr_code("!onlykey") == {}


def test_to_base36():
    assert to_base36(0) == "0"
    assert to_base36(35) == "z"
    assert to_base36(36) == "10"
