"""utils.py 测试：session 存取 roundtrip（不涉网）"""

import requests

from xmu_rollcall.utils import save_session, load_session


def test_session_roundtrip(tmp_path):
    path = str(tmp_path / "sess.json")
    s1 = requests.Session()
    s1.cookies.set("a", "1", domain="lnt.xmu.edu.cn", path="/")
    save_session(s1, path)

    s2 = requests.Session()
    assert load_session(s2, path) is True
    assert s2.cookies.get("a") == "1"


def test_load_session_corrupt(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("not json{{{", encoding="utf-8")
    assert load_session(requests.Session(), str(path)) is False
