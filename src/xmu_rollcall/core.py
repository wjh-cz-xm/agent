"""v5 编排核心：推送监听 + 轮询兜底 + Web UI + 人工确认签到。

线程模型：
- 主线程 = asyncio 事件循环（_main）：push 监听、轮询兜底、实时人数、
  数字码取码、QR 串行 worker、过期清理、签到执行。
- confirm 分发线程（daemon）：阻塞读 confirm_queue → call_soon_threadsafe
  回到主循环执行 _answer_rollcall。⚠️ 阻塞 queue.get 绝不放进事件循环
  的执行器（asyncio.run 关闭时会永久挂起）。
- Flask UI 线程（daemon）：只读 state + 入队确认请求。

所有同步 requests 调用（登录、取码、人数统计、雷达、QR）都经
asyncio.to_thread 执行，不阻塞事件循环。
"""

import asyncio
import socket
import sys
import threading
import time

import requests

from . import __version__
from . import push
from . import ui
from ._xmulogin import xmulogin
from .config import (
    get_cookies_path,
    get_poll_interval,
    get_session_meta_path,
    load_config,
)
from .logger import get_recent, log_rollcall
from .qr_handler import send_qr
from .rollcall_handler import classify, extract_rollcalls, type_label
from .state import AppState
from .utils import (
    base_url,
    fetch_profile_meta,
    headers,
    load_session,
    load_session_meta,
    save_session,
    save_session_meta,
)
from .verify import answer_number_code_async, get_number_code, get_signed_count, send_radar

UI_PORT = 5000

# 实时已签人数的刷新间隔（秒）
LIVE_COUNT_INTERVAL = 3


# ---------- 启动（同步部分） ----------

def _port_free(port):
    """探测 127.0.0.1:port 是否可用"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _setup_session(account):
    """恢复或新建登录会话，返回 (session, meta)；登录失败返回 (None, {})。

    meta 由 fetch_profile_meta 产生：非空即会话有效；session_id 可能为
    None（服务器未下发 X-SESSION-ID），此时推送停用、轮询兜底。
    """
    account_id = account.get("id", 1)
    cookies_path = get_cookies_path(account_id)
    session = requests.Session()

    if load_session(session, cookies_path):
        meta = fetch_profile_meta(session)
        if meta:
            name = meta["profile"].get("name") or account.get("name", "")
            print(f"Session restored successfully, Welcome, {name}")
            return session, meta
        print("会话已失效，重新登录...")

    time.sleep(2)
    session = xmulogin(type=3, username=account["username"], password=account["password"])
    if not session:
        return None, {}
    save_session(session, cookies_path)
    meta = fetch_profile_meta(session)
    name = (meta.get("profile") or {}).get("name") or account.get("name", "")
    print(f"Login successful, Welcome, {name}")
    return session, meta


def _start_confirm_dispatcher(state, handler=None):
    """confirm_queue → core loop 的分发线程（daemon，阻塞 get 安全）。

    handler：签名 (state, req) -> awaitable；默认 _answer_rollcall，
    演示模式传入自己的模拟处理函数。
    """
    handler = handler or _answer_rollcall

    def _run():
        while True:
            req = state.confirm_queue.get()
            if state.loop is None:
                continue
            state.loop.call_soon_threadsafe(
                lambda: asyncio.create_task(handler(state, req))
            )

    threading.Thread(target=_run, daemon=True).start()


def start_app(account, open_browser=True):
    """启动签到监控（CLI `xmu start` 的入口）。"""
    sys.stdout.reconfigure(encoding="utf-8")

    if not _port_free(UI_PORT):
        print(f"端口 {UI_PORT} 被占用，无法启动 Web UI（本程序的控制界面）。"
              f"请关闭占用该端口的进程后重试。")
        sys.exit(1)

    session, meta = _setup_session(account)
    if session is None:
        print("登录失败，5 秒后退出。")
        time.sleep(5)
        sys.exit(1)

    state = AppState(
        account_name=account.get("name") or account.get("username", ""),
        session=session,
        poll_interval=get_poll_interval(),
    )
    state.meta_path = get_session_meta_path(account.get("id"))

    # X-SESSION-ID：优先本次 profile 响应头，缺失时回退 meta 文件缓存
    sid = (meta or {}).get("session_id") or (load_session_meta(state.meta_path) or {}).get("session_id")
    user_id = (meta or {}).get("user_id")
    if sid and user_id is not None:
        state.session_id = sid
        state.user_id = user_id
        state.push_enabled = True
        session.headers["X-SESSION-ID"] = sid
    else:
        state.push_enabled = False
        print("[push] 未取得 X-SESSION-ID，推送通道停用，轮询兜底生效。")
    if meta:
        save_session_meta(state.meta_path, {
            "user_id": user_id,
            "session_id": sid,
            "updated_at": time.time(),
        })

    state.history = get_recent(50)

    ui.run_ui(state, open_browser=open_browser)
    _start_confirm_dispatcher(state)
    print(f"xmu-rollcall v{__version__} 启动。"
          f"Web UI: http://127.0.0.1:{UI_PORT}（Ctrl+C 退出）")

    try:
        asyncio.run(_main(state))
    finally:
        state.stop_event.set()
        print("已停止。")


# ---------- 事件循环任务 ----------

async def _main(state):
    state.loop = asyncio.get_running_loop()
    tasks = [
        asyncio.create_task(_poll_task(state)),
        asyncio.create_task(_live_count_task(state)),
        asyncio.create_task(_qr_worker(state)),
        asyncio.create_task(_prune_task(state)),
    ]
    if state.push_enabled:
        tasks.append(asyncio.create_task(push.push_listener(state, lambda: check_now(state))))
    try:
        while not state.stop_event.is_set():
            await asyncio.sleep(0.5)
    finally:
        # KeyboardInterrupt 取消本任务时也会走到这里，保证 stop_event
        # 被置位（send_qr 线程靠它快速退出，避免 executor 排空挂起）
        state.stop_event.set()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def check_now(state):
    """拉取当前签到列表并注册新签到（推送事件与轮询兜底的共同入口）。"""
    if state.checking:
        return
    state.checking = True
    try:
        resp = await asyncio.to_thread(
            state.session.get, f"{base_url}/api/radar/rollcalls", headers=headers
        )
        data = resp.json()
    except Exception as e:
        print(f"[poll] 拉取签到列表失败: {e}")  # 不崩 loop（旧版会直接退出）
        return
    finally:
        state.checking = False
    state.query_count += 1

    # 机会式补捕 X-SESSION-ID（推送通道自愈）
    if state.session_id is None and state.push_enabled is False:
        sid = resp.headers.get("X-SESSION-ID")
        if sid and state.user_id is not None:
            state.session_id = sid
            state.push_enabled = True
            state.session.headers["X-SESSION-ID"] = sid
            if state.meta_path:
                save_session_meta(state.meta_path, {
                    "user_id": state.user_id,
                    "session_id": sid,
                    "updated_at": time.time(),
                })
            print("[push] X-SESSION-ID 已捕获，推送通道启用。")
            asyncio.create_task(push.push_listener(state, lambda: check_now(state)))

    _, rollcalls = extract_rollcalls(data)
    for rc in rollcalls:
        rid = str(rc.get("rollcall_id") or "")
        if not rid:
            continue

        card = state.get_card(rid)
        if card is not None:
            # 刷新已有卡片：本人已签 / 签到过期
            active = card.get("status") in ("pending", "auto_scanning")
            if rc.get("status") == "on_call_fine" and active:
                state.mark_terminal(rid, "already", True, detail="已签到过", log_type="已签到")
                await asyncio.to_thread(
                    log_rollcall, card["course_title"], "已签到", "success", detail="已签到过"
                )
            elif rc.get("is_expired") and active:
                state.mark_terminal(rid, "expired", False, detail="签到已过期")
            continue

        # 新签到：只注册 absent（未签）且未过期的
        if rc.get("status") != "absent" or rc.get("is_expired"):
            continue
        kind = classify(rc)
        if state.register_card(rc, kind, type_label(kind)):
            print(f"[check] 新签到: {rc.get('course_title')}（{type_label(kind)}）")
            if kind == "number":
                asyncio.create_task(_fetch_code_task(state, rid))
            elif kind == "qr":
                state.qr_queue.put_nowait(rid)


async def _fetch_code_task(state, rid):
    """数字码取码任务：最多 3 次尝试，间隔 5s；卡片被处理即中止。"""
    for attempt in range(3):
        if state.stop_event.is_set():
            return
        card = state.get_card(rid)
        if card is None or card.get("status") != "pending":
            return
        code = await asyncio.to_thread(get_number_code, state.session, rid)
        if code:
            state.update_card(rid, code_status="fetched", code=code)
            print(f"[code] 签到 {rid} 取码成功: {code}")
            return
        if attempt < 2:
            await asyncio.sleep(5)
    state.update_card(rid, code_status="failed")
    print(f"[code] 签到 {rid} 取码失败，点击签到后将暴力枚举。")


async def _live_count_task(state):
    """实时已签人数：每 3s 对所有待确认卡片统计一次。"""
    while not state.stop_event.is_set():
        await asyncio.sleep(LIVE_COUNT_INTERVAL)
        active = state.pending_ids()
        if not active:
            continue
        results = await asyncio.gather(
            *(asyncio.to_thread(get_signed_count, state.session, rid) for rid in active),
            return_exceptions=True,
        )
        for rid, result in zip(active, results):
            if isinstance(result, tuple) and result[0] is not None:
                state.update_card(rid, signed_count=result[0])


async def _qr_worker(state):
    """二维码签到 worker：串行执行 send_qr（ngrok 单隧道 + 端口 5001，不可并发）。"""
    while not state.stop_event.is_set():
        try:
            rid = await asyncio.wait_for(state.qr_queue.get(), timeout=1)
        except asyncio.TimeoutError:
            continue
        card = state.get_card(rid)
        if card is None:
            continue
        state.update_card(rid, status="auto_scanning")
        ngrok_token = load_config().get("ngrok_token", "")
        ok = await asyncio.to_thread(
            send_qr,
            state.session,
            rid,
            ngrok_token,
            stop_event=state.stop_event,
            on_link=lambda link, _rid=rid: state.update_card(_rid, scan_url=link),
            course_title=card["course_title"],
        )
        if state.stop_event.is_set():
            return
        status = "success" if ok else "failure"
        detail = "扫码签到成功" if ok else "扫码签到失败"
        state.mark_terminal(rid, status, ok, detail=detail)
        await asyncio.to_thread(
            log_rollcall, card["course_title"], card["type_label"], status, detail=detail
        )
        print(f"[qr] {card['course_title']} {detail}")


async def _prune_task(state):
    """过期卡片/去重表定期清理。"""
    while not state.stop_event.is_set():
        await asyncio.sleep(60)
        state.prune()


async def _poll_task(state):
    """轮询兜底：推送为主，本任务保证推送失效时签到仍能被发现。"""
    while not state.stop_event.is_set():
        await check_now(state)
        await asyncio.sleep(state.poll_interval)


async def _answer_rollcall(state, req):
    """执行签到（用户点击『签到』后由 confirm 分发线程调度）。

    双击防护：卡片必须存在且 status == pending。
    """
    rid = str(req.get("rollcall_id") or "")
    card = state.get_card(rid)
    if card is None or card.get("status") != "pending":
        return
    kind = card.get("kind")
    if kind not in ("number", "radar"):
        return  # qr 走自动流程，不响应人工按钮
    state.update_card(rid, status="answering")

    try:
        if kind == "number":
            known = card.get("code") if card.get("code_status") == "fetched" else None
            result = await answer_number_code_async(state.session, rid, known_code=known)
            ok = bool(result)
            code = result or ""
        else:
            ok = await asyncio.to_thread(send_radar, state.session, rid)
            code = ""
    except Exception as e:
        print(f"[answer] 签到 {rid} 执行异常: {e}")
        ok, code = False, ""

    status = "success" if ok else "failure"
    detail = "签到成功" if ok else "签到失败"
    state.mark_terminal(rid, status, ok, code=code, detail=detail)
    await asyncio.to_thread(
        log_rollcall, card["course_title"], card["type_label"], status, detail=detail, code=code
    )
    print(f"[answer] {card['course_title']} {detail}")
