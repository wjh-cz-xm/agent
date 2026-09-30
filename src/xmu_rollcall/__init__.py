"""XMU Rollcall - 厦大畅课（TronClass）自动签到

既可作为 CLI 工具（`xmu` 命令）使用，也可作为库导入核心逻辑：

    >>> from xmu_rollcall.verify import get_number_code, send_code
    >>> from xmu_rollcall.core import start_app
    >>> from xmu_rollcall._xmulogin import xmulogin  # 内置 CAS 登录 SDK

版本号唯一来源为本文件，CLI 横幅与版本检查均引用它。
"""

__version__ = "5.0.0"
