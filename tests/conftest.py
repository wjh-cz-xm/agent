"""pytest 公共设施。

重要：在任何 xmu_rollcall 模块导入之前，把配置目录指到临时目录，
确保测试绝不读取/写入真实的 ~/.xmu_rollcall（config 模块在导入时
就会执行 get_config_dir() 的目录探测）。
"""

import os
import tempfile

_os = os

_TEST_CONFIG_DIR = _os.environ["XMU_ROLLCALL_CONFIG_DIR"] = tempfile.mkdtemp(
    prefix="xmu_rollcall_test_"
)

import pytest  # noqa: E402


class FakeResponse:
    """模拟 requests.Response 中被本项目用到的部分"""

    def __init__(self, status_code=200, json_data=None, headers=None):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}
        self.headers = headers if headers is not None else {}

    def json(self):
        return self._json


class FakeSession:
    """模拟 requests.Session 中被本项目用到的部分。

    get_resp / put_resp 可以是 FakeResponse、异常，或 FakeResponse 列表
    （列表时按调用顺序逐个弹出，模拟重试场景）。
    """

    def __init__(self, get_resp=None, put_resp=None):
        self.headers = {"User-Agent": "fake"}
        self.cookies = {}
        self._get_resp = get_resp
        self._put_resp = put_resp
        self.get_calls = []
        self.put_calls = []

    @staticmethod
    def _pick(resp):
        if isinstance(resp, list):
            return resp.pop(0) if resp else FakeResponse(500, {})
        return resp

    def get(self, url, headers=None, timeout=None):
        self.get_calls.append({"url": url, "headers": headers, "timeout": timeout})
        resp = self._pick(self._get_resp)
        if isinstance(resp, Exception):
            raise resp
        return resp if resp is not None else FakeResponse()

    def put(self, url, json=None, headers=None, timeout=None):
        self.put_calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        resp = self._pick(self._put_resp)
        if isinstance(resp, Exception):
            raise resp
        return resp if resp is not None else FakeResponse()


@pytest.fixture
def make_session():
    """构造 FakeSession 的工厂"""
    def _make(get_resp=None, put_resp=None):
        return FakeSession(get_resp=get_resp, put_resp=put_resp)
    return _make
