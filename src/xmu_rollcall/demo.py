"""演示模式：模拟一次数字码签到，预览 Web UI 效果（`xmu demo`）。

全程不联网：假 session + 本地模拟任务，真实 config/cookie/日志均不被写入。
模拟节奏：
- 注册卡片后 2s 取码成功（先看到「正在取码…」再出四位码）
- 已签人数从 23 人起每 3s +1（模拟同学陆续签到）
- 点击「签到」→ answering 1.2s → 成功（若取码未完成就点了，模拟暴力枚举命中）
- 历史记录只进内存（state.history），不写 rollcall_log.json
"""

import asyncio
import random
import threading
import time

from . import __version__
from . import ui
from .core import UI_PORT, _port_free, _start_confirm_dispatcher
from .logger import get_recent
from .state import AppState

DEMO_ROLLCALL_ID = "demo-1"
DEMO_COURSE = "演示课程（模拟签到）"
DEMO_CODE = "3456"
DEMO_INITIAL_SIGNED = 23
DEMO_CODE_DELAY = 2      # 取码耗时（秒）
DEMO_ANSWER_DELAY = 1.2  # 模拟提交耗时（秒）
DEMO_COUNT_INTERVAL = 3  # 模拟同学签到的间隔（秒）


class _FakeSession:
    """演示模式的假 session：任何网络调用都不该发生"""

    headers = {}


def make_demo_state():
    """构造演示状态：假 session、真实历史记录（只读加载）"""
    state = AppState(
        account_name="演示账号",
        session=_FakeSession(),
        poll_interval=15,
        push_enabled=False,
    )
    state.history = get_recent(50)
    return state


def register_demo_rollcall(state):
    """注册一张数字码签到卡片（与真实签到同构的原始数据）"""
    state.register_card({
        "course_title": DEMO_COURSE,
        "created_by_name": "模拟教师",
        "department_name": "演示学院",
        "is_expired": False,
        "is_number": True,
        "is_radar": False,
        "rollcall_id": DEMO_ROLLCALL_ID,
        "rollcall_status": "active",
        "scored": False,
        "status": "absent",
    }, "number", "数字码签到")
    state.update_card(DEMO_ROLLCALL_ID, signed_count=DEMO_INITIAL_SIGNED)


async def _demo_code_task(state):
    """模拟取码：延迟后出码"""
    await asyncio.sleep(DEMO_CODE_DELAY)
    card = state.get_card(DEMO_ROLLCALL_ID)
    if card is None or card.get("status") != "pending":
        return
    state.update_card(DEMO_ROLLCALL_ID, code_status="fetched", code=DEMO_CODE)
    print(f"[demo] 取码成功: {DEMO_CODE}")


async def _demo_count_task(state):
    """模拟同学陆续签到：已签人数每 3s +1"""
    while not state.stop_event.is_set():
        await asyncio.sleep(DEMO_COUNT_INTERVAL)
        card = state.get_card(DEMO_ROLLCALL_ID)
        if card is None or card.get("status") != "pending":
            continue
        current = card.get("signed_count") or 0
        state.update_card(DEMO_ROLLCALL_ID, signed_count=current + 1)


async def _demo_answer(state, req):
    """模拟提交签到（挂在 confirm 分发线程上，与真实 _answer_rollcall 同入口）"""
    rid = str(req.get("rollcall_id") or "")
    card = state.get_card(rid)
    if card is None or card.get("status") != "pending":
        return
    state.update_card(rid, status="answering")
    await asyncio.sleep(DEMO_ANSWER_DELAY)

    if card.get("code_status") == "fetched" and card.get("code"):
        code = card["code"]
        detail = "模拟提交成功"
    else:
        code = str(random.randint(0, 9999)).zfill(4)
        detail = "模拟暴力枚举命中"
    state.mark_terminal(rid, "success", True, code=code, detail=detail)
    print(f"[demo] {detail}: {code}（未写真实日志）")


async def _demo_main(state):
    state.loop = asyncio.get_running_loop()
    tasks = [
        asyncio.create_task(_demo_code_task(state)),
        asyncio.create_task(_demo_count_task(state)),
    ]
    try:
        while not state.stop_event.is_set():
            await asyncio.sleep(0.5)
    finally:
        state.stop_event.set()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def start_demo(open_browser=True):
    """启动演示模式（CLI `xmu demo` 入口）"""
    if not _port_free(UI_PORT):
        print(f"端口 {UI_PORT} 被占用，请先停止正在运行的其他实例。")
        return 1

    state = make_demo_state()
    register_demo_rollcall(state)
    print(f"xmu-rollcall v{__version__} 演示模式：模拟一次数字码签到。")
    print(f"Web UI: http://127.0.0.1:{UI_PORT}（Ctrl+C 退出）")
    print("观察：取码中 → 四位码；已签人数每 3s +1；点「签到」模拟提交。")

    ui.run_ui(state, open_browser=open_browser)
    _start_confirm_dispatcher(state, handler=_demo_answer)

    try:
        asyncio.run(_demo_main(state))
    finally:
        state.stop_event.set()
        print("演示结束。")
    return 0
