"""持久的“过半自动签到”安全开关。

状态按绝对 Unix 时间到期，程序退出、重启或休眠都不会暂停 12 小时计时。
文件只保存开关和时间，不包含账号、Cookie 或其他凭据。
"""

import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from .config import CONFIG_DIR, ensure_config_dir


DEFAULT_TTL_SECONDS = 12 * 60 * 60
STATE_FILE = Path(os.environ.get("XMU_ROLLCALL_AUTO_SIGN_PATH") or (CONFIG_DIR / "auto_sign.json"))


def _status(data, current, state_path):
    try:
        expires_at = float(data.get("expires_at") or 0)
    except (TypeError, ValueError, AttributeError):
        expires_at = 0
    enabled = bool(data.get("enabled") is True and expires_at > current)
    remaining = max(0, int(expires_at - current)) if enabled else 0
    return {
        "enabled": enabled,
        "expires_at": expires_at if enabled else None,
        "expires_at_iso": (
            datetime.fromtimestamp(expires_at, timezone.utc).astimezone().isoformat()
            if enabled else None
        ),
        "remaining_seconds": remaining,
        "duration_seconds": DEFAULT_TTL_SECONDS,
        "state_path": str(state_path),
    }


def get_status(path=None, current=None):
    """读取当前开关；文件缺失、损坏或已过期时一律视为关闭。"""
    target = Path(path) if path else STATE_FILE
    current = time.time() if current is None else float(current)
    try:
        data = json.loads(target.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError, TypeError):
        data = {}
    return _status(data, current, target)


def _write(data, path=None):
    target = Path(path) if path else STATE_FILE
    if target == STATE_FILE:
        ensure_config_dir()
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, target)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def enable(path=None, current=None, ttl_seconds=DEFAULT_TTL_SECONDS):
    """从本次操作起开启 12 小时；再次开启会重新计算到期时间。"""
    current = time.time() if current is None else float(current)
    ttl_seconds = int(ttl_seconds)
    if ttl_seconds <= 0:
        raise ValueError("ttl_seconds must be positive")
    _write({"enabled": True, "updated_at": current, "expires_at": current + ttl_seconds}, path)
    return get_status(path, current)


def disable(path=None, current=None):
    """立即关闭。"""
    current = time.time() if current is None else float(current)
    _write({"enabled": False, "updated_at": current, "expires_at": 0}, path)
    return get_status(path, current)
