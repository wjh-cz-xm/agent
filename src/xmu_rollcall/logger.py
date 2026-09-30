import json
import os
import time
from .config import CONFIG_DIR

LOG_FILE = CONFIG_DIR / "rollcall_log.json"
MAX_LOG_ENTRIES = 100


def log_rollcall(course_title, rollcall_type, status, detail="", code=""):
    """记录签到日志"""
    log_path = str(LOG_FILE)
    logs = []
    if os.path.exists(log_path):
        try:
            with open(log_path, "r", encoding="utf-8") as f:
                logs = json.load(f)
        except Exception:
            logs = []

    entry = {
        "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
        "timestamp": time.time(),
        "course": course_title,
        "type": rollcall_type,
        "status": status,
        "code": code,
        "detail": detail,
    }
    logs.append(entry)

    if len(logs) > MAX_LOG_ENTRIES:
        logs = logs[-MAX_LOG_ENTRIES:]

    try:
        with open(log_path, "w", encoding="utf-8") as f:
            json.dump(logs, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def get_recent_successful(n=2):
    """获取最近 N 条成功记录"""
    log_path = str(LOG_FILE)
    if not os.path.exists(log_path):
        return []
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            logs = json.load(f)
        successful = [entry for entry in logs if entry.get("status") == "success"]
        return successful[-n:]
    except Exception:
        return []


def get_recent(n=20):
    """获取最近 N 条记录（全部状态，倒序），供 Web UI 历史表使用"""
    log_path = str(LOG_FILE)
    if not os.path.exists(log_path):
        return []
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            logs = json.load(f)
        return logs[-n:][::-1]
    except Exception:
        return []
