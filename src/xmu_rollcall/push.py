"""ntf pubsub WebSocket 推送监听（Atmosphere 协议）。

参考上游 SDK（xmu-tronclass-sdk/tronclass/api/push.py）：帧格式为
`x|x|x|{json}`，第 4 段是 JSON；`msg["type"]` 含 "rollcall"（忽略
大小写）即签到事件。服务器会下发 `X` / `|X|` 心跳帧，被帧解析自然
过滤；客户端不发心跳，靠断线重连兜底。

推送事件只作 wake-up，不直接采信 payload——core 收到事件后照常
拉取 /api/radar/rollcalls 获取规范数据并按 rollcall_id 去重。
"""

import asyncio
import json
import time

import aiohttp

from .utils import base_url

PUSH_RECONNECT_DELAY = 5      # 断线重连等待（秒）
NO_SESSION_RETRY_DELAY = 30   # 无 X-SESSION-ID 时的重试间隔（秒）


def build_push_url(user_id, session_id):
    """构造 ntf pubsub WebSocket 地址（参数顺序与上游 SDK 一致）"""
    host = base_url.split("://", 1)[-1]
    return (
        f"wss://{host}/ntf/pubsub/{user_id}"
        "?X-Atmosphere-tracking-id=0"
        "&X-Atmosphere-Transport=websocket"
        "&Content-Type=application%2Fjson"
        "&X-atmo-protocol=true"
        f"&X-SESSION-ID={session_id}"
    )


def parse_push_frame(text):
    """解析一条 Atmosphere 帧，返回第 4 段的 JSON dict；非数据帧返回 None。

    <4 段或第 4 段为空 → None（过滤服务器心跳帧）；
    第 4 段不是合法 JSON / 不是 dict → None。
    """
    if not isinstance(text, str):
        return None
    parts = text.split("|", 3)
    if len(parts) < 4 or not parts[3]:
        return None
    try:
        msg = json.loads(parts[3])
    except (json.JSONDecodeError, ValueError):
        return None
    return msg if isinstance(msg, dict) else None


def is_rollcall_event(msg):
    """msg["type"] 含 "rollcall"（忽略大小写）即签到事件"""
    return "rollcall" in str(msg.get("type", "")).lower()


def extract_rollcall_hint(msg):
    """从推送 payload 尽力提取签到线索（防御式，绝不抛异常）。

    payload 结构随版本可能变化；hint 仅供日志展示，规范数据由
    core 拉取 /api/radar/rollcalls 获得。
    """

    def _search(node, depth=0):
        if depth > 4 or not isinstance(node, (dict, list)):
            return {}
        if isinstance(node, list):
            for item in node:
                found = _search(item, depth + 1)
                if found:
                    return found
            return {}
        rid = node.get("rollcall_id")
        if rid is None and depth > 0:
            # 顶层 "id" 可能是通知自身的 id，不采信；嵌套层允许
            rid = node.get("id")
        if rid is not None:
            return {
                "rollcall_id": rid,
                "course_title": node.get("course_title", ""),
                "created_by_name": node.get("created_by_name", ""),
                "department_name": node.get("department_name", ""),
            }
        for key in ("data", "content", "rollcall", "message", "rollcalls"):
            found = _search(node.get(key), depth + 1)
            if found:
                return found
        # 兜底：结构漂移时遍历其余任意键
        for value in node.values():
            if value is node:
                continue
            found = _search(value, depth + 1)
            if found:
                return found
        return {}

    try:
        return _search(msg)
    except Exception:
        return {}


async def push_listener(state, wake):
    """ntf 推送监听循环。

    - wake：无参 async 回调（core 的 check_now 包装）。事件到达时
      create_task 调度，绝不 await（不阻塞 socket 读取）。
    - 断线（异常或服务器关闭连接）→ 5s 后重连。
    - 无 X-SESSION-ID（state.session_id 为 None）→ 每 30s 重查
      （core 可能在轮询响应头里机会式补捕后写入）。
    - state.stop_event 触发 → 退出。
    """
    while not state.stop_event.is_set():
        if not state.session_id:
            await asyncio.sleep(NO_SESSION_RETRY_DELAY)
            continue
        url = build_push_url(state.user_id, state.session_id)
        headers = {"X-SESSION-ID": state.session_id}
        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(trust_env=False) as client:
                async with client.ws_connect(
                    url, headers=headers, heartbeat=None, timeout=timeout
                ) as ws:
                    state.push_connected = True
                    print("[push] connected")
                    async for msg in ws:
                        if state.stop_event.is_set():
                            break
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            parsed = parse_push_frame(msg.data)
                            if parsed and is_rollcall_event(parsed):
                                state.push_last_event = time.time()
                                hint = extract_rollcall_hint(parsed)
                                print(
                                    f"[push] rollcall event: "
                                    f"{hint or {'type': parsed.get('type')}}"
                                )
                                asyncio.create_task(wake())
                        elif msg.type in (
                            aiohttp.WSMsgType.CLOSED,
                            aiohttp.WSMsgType.ERROR,
                        ):
                            break
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[push] disconnected: {e}, "
                  f"retrying in {PUSH_RECONNECT_DELAY}s...")
        state.push_connected = False
        await asyncio.sleep(PUSH_RECONNECT_DELAY)
