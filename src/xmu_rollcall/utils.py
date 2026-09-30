import os
import json
import requests

base_url = "https://lnt.xmu.edu.cn"
headers = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

def clear_screen():
    """清屏"""
    if os.name == 'nt':
        os.system('cls')
    else:
        os.system('clear')

def save_session(sess: requests.Session, path: str):
    """保存session到文件"""
    try:
        cj_dict = requests.utils.dict_from_cookiejar(sess.cookies)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cj_dict, f)
    except Exception:
        pass

def load_session(sess: requests.Session, path: str):
    """从文件加载session"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            cj_dict = json.load(f)
        sess.cookies = requests.utils.cookiejar_from_dict(cj_dict)
        return True
    except Exception:
        return False

def verify_session(sess: requests.Session) -> dict:
    """验证session是否有效"""
    try:
        resp = sess.get(f"{base_url}/api/profile", headers=headers)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, dict) and "name" in data:
                return data
    except Exception:
        pass
    return {}

def fetch_profile_meta(sess: requests.Session) -> dict:
    """拉取个人资料，并提取推送通道所需的会话元数据。

    返回 {"profile": {...}, "user_id": ..., "session_id": ...}；
    请求失败或响应不含 name 时返回 {}。
    X-SESSION-ID 只存在于 /api/profile 的响应头（登录响应中不提供），
    用法与上游 SDK 一致。
    """
    try:
        resp = sess.get(f"{base_url}/api/profile", headers=headers)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, dict) and "name" in data:
                return {
                    "profile": data,
                    "user_id": data.get("id"),
                    "session_id": resp.headers.get("X-SESSION-ID"),
                }
    except Exception:
        pass
    return {}

def save_session_meta(path: str, meta: dict):
    """保存会话元数据（X-SESSION-ID / user_id）到 JSON 文件"""
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False)
    except Exception:
        pass

def load_session_meta(path: str):
    """读取会话元数据；文件缺失/损坏/非 dict 返回 None"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        return meta if isinstance(meta, dict) else None
    except Exception:
        return None
