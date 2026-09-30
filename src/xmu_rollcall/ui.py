"""Web UI（Flask 单页仪表盘，黑灰极简）。

127.0.0.1:5000，daemon 线程运行。UI 侧只读 state（/api/state）并
入队确认请求（/api/answer），一切业务写操作都在 core 循环内完成。
"""

import logging
import os
import threading
import time
import webbrowser

from flask import Flask, jsonify, render_template, request

from .auto_sign import disable as disable_auto_sign
from .auto_sign import enable as enable_auto_sign
from .auto_sign import get_status as get_auto_sign_status

_template_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
app = Flask(__name__, template_folder=_template_dir)

# 绑定的共享状态（core 启动时 bind；测试直接替换后可用 test_client）
state = None
confirm_queue = None


def bind(app_state, queue):
    global state, confirm_queue
    state = app_state
    confirm_queue = queue


@app.route("/")
def dashboard():
    return render_template("dashboard.html")


@app.route("/api/state")
def api_state():
    if state is None:
        return jsonify({"error": "not ready"}), 503
    snapshot = state.snapshot()
    snapshot["system"]["auto_sign"] = get_auto_sign_status()
    return jsonify(snapshot)


@app.route("/api/auto-sign", methods=["POST"])
def api_auto_sign():
    """本机 UI 安全开关；开启后按绝对时间 12 小时自动失效。"""
    if state is None:
        return jsonify({"ok": False, "message": "not ready"}), 503
    data = request.get_json(silent=True) or {}
    enabled = data.get("enabled")
    if not isinstance(enabled, bool):
        return jsonify({"ok": False, "message": "enabled 必须是布尔值"}), 400
    status = enable_auto_sign() if enabled else disable_auto_sign()
    return jsonify({"ok": True, "auto_sign": status})


@app.route("/api/answer", methods=["POST"])
def api_answer():
    if state is None or confirm_queue is None:
        return jsonify({"ok": False, "message": "not ready"}), 503
    data = request.get_json(silent=True) or {}
    rid = str(data.get("rollcall_id") or "")
    card = state.get_card(rid)
    if card is None:
        return jsonify({"ok": False, "message": "签到不存在"}), 404
    if card.get("status") != "pending":
        return jsonify({"ok": False, "message": "当前状态不可签到"}), 409
    confirm_queue.put({"rollcall_id": rid, "ts": time.time()})
    return jsonify({"ok": True})


@app.route("/api/dismiss", methods=["POST"])
def api_dismiss():
    if state is None:
        return jsonify({"error": "not ready"}), 503
    data = request.get_json(silent=True) or {}
    rid = str(data.get("rollcall_id") or "")
    state.dismiss_card(rid)
    return jsonify({"ok": True})


def run_ui(app_state, queue=None, port=5000, open_browser=True):
    """在 daemon 线程启动 UI 服务器，返回线程对象。"""
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    bind(app_state, queue or app_state.confirm_queue)
    thread = threading.Thread(
        target=app.run,
        kwargs={
            "host": "127.0.0.1",
            "port": port,
            "debug": False,
            "use_reloader": False,
            "threaded": True,
        },
        daemon=True,
    )
    thread.start()
    if open_browser:
        try:
            webbrowser.open(f"http://127.0.0.1:{port}/")
        except Exception:
            pass
    return thread
