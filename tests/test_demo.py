"""demo.py 测试：演示模式的状态构造与模拟流程（全离线，不写真实日志）"""

import asyncio

import xmu_rollcall.demo as demo


def test_make_demo_state_and_register():
    state = demo.make_demo_state()
    demo.register_demo_rollcall(state)
    card = state.get_card(demo.DEMO_ROLLCALL_ID)
    assert card["kind"] == "number"
    assert card["code_status"] == "fetching"
    assert card["signed_count"] == demo.DEMO_INITIAL_SIGNED
    assert state.push_enabled is False  # 演示模式不连推送


def test_demo_code_task_sets_code(monkeypatch):
    state = demo.make_demo_state()
    demo.register_demo_rollcall(state)
    monkeypatch.setattr(demo, "DEMO_CODE_DELAY", 0.01)
    asyncio.run(demo._demo_code_task(state))
    card = state.get_card(demo.DEMO_ROLLCALL_ID)
    assert card["code_status"] == "fetched"
    assert card["code"] == demo.DEMO_CODE


def test_demo_count_task_increments(monkeypatch):
    state = demo.make_demo_state()
    demo.register_demo_rollcall(state)
    monkeypatch.setattr(demo, "DEMO_COUNT_INTERVAL", 0.01)

    async def run():
        task = asyncio.create_task(demo._demo_count_task(state))
        await asyncio.sleep(0.1)
        state.stop_event.set()
        await task

    asyncio.run(run())
    assert state.get_card(demo.DEMO_ROLLCALL_ID)["signed_count"] >= demo.DEMO_INITIAL_SIGNED + 1


def test_demo_answer_with_fetched_code(monkeypatch):
    state = demo.make_demo_state()
    demo.register_demo_rollcall(state)
    state.update_card(demo.DEMO_ROLLCALL_ID, code_status="fetched", code="1234")
    monkeypatch.setattr(demo, "DEMO_ANSWER_DELAY", 0.01)
    asyncio.run(demo._demo_answer(state, {"rollcall_id": demo.DEMO_ROLLCALL_ID}))
    card = state.get_card(demo.DEMO_ROLLCALL_ID)
    assert card["status"] == "success"
    assert card["result"]["code"] == "1234"
    assert "模拟提交成功" in card["result"]["detail"]
    assert state.history[0]["status"] == "success"  # 只进内存历史


def test_demo_answer_early_click_simulates_bruteforce(monkeypatch):
    """取码还没完成就点签到 → 模拟暴力枚举命中（4 位随机码）"""
    state = demo.make_demo_state()
    demo.register_demo_rollcall(state)
    # code_status 仍是 fetching
    monkeypatch.setattr(demo, "DEMO_ANSWER_DELAY", 0.01)
    asyncio.run(demo._demo_answer(state, {"rollcall_id": demo.DEMO_ROLLCALL_ID}))
    card = state.get_card(demo.DEMO_ROLLCALL_ID)
    assert card["status"] == "success"
    assert len(card["result"]["code"]) == 4 and card["result"]["code"].isdigit()
    assert "暴力枚举" in card["result"]["detail"]


def test_demo_answer_double_click_guard(monkeypatch):
    state = demo.make_demo_state()
    demo.register_demo_rollcall(state)
    state.update_card(demo.DEMO_ROLLCALL_ID, status="answering")
    monkeypatch.setattr(demo, "DEMO_ANSWER_DELAY", 0.01)
    asyncio.run(demo._demo_answer(state, {"rollcall_id": demo.DEMO_ROLLCALL_ID}))
    assert state.get_card(demo.DEMO_ROLLCALL_ID)["status"] == "answering"  # 未被覆盖
