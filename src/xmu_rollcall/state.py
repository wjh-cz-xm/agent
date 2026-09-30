"""共享应用状态（AppState）。

core 事件循环与 Flask UI 线程之间的唯一共享面：UI 侧只读（snapshot）
或入队确认请求，core 侧负责一切写操作。所有可变字段用一把
threading.Lock 保护。
"""

import asyncio
import queue
import threading
import time

from . import __version__

# 终态：不再接受任何操作，只等待用户关闭（dismiss）或超时清理
TERMINAL_STATUSES = {"success", "failure", "expired", "already"}

# 终态卡片保留时长 / 已处理 id 去重时长（秒）
TERMINAL_CARD_TTL = 600
HANDLED_ID_TTL = 1200


class AppState:
    """签到监控的共享状态。

    session / session_id / user_id 由 core.start_app 在启动时设置；
    cards 是 rollcall_id -> 卡片 dict 的映射（多签到并行，每张独立可签）。
    """

    def __init__(self, account_name="", session=None, poll_interval=15,
                 push_enabled=True, session_id=None, user_id=None, meta_path=None):
        self.account_name = account_name
        self.session = session
        self.session_id = session_id
        self.user_id = user_id
        self.poll_interval = poll_interval
        self.push_enabled = push_enabled
        self.meta_path = meta_path  # 会话 meta 文件路径（core 写入）

        self.start_time = time.time()
        self.query_count = 0
        self.push_connected = False
        self.push_last_event = None
        self.checking = False  # check_now 重入保护（仅 core 循环内访问）

        self._lock = threading.Lock()
        self.cards = {}
        self.handled_ids = {}  # rollcall_id -> 处理时间戳（去重）
        self.history = []      # 与 rollcall_log.json 同构的记录，倒序

        self.stop_event = threading.Event()
        self.confirm_queue = queue.Queue()  # UI 线程 -> confirm 分发线程
        self.qr_queue = asyncio.Queue()     # core 循环内：待处理的 QR 签到（串行）
        self.loop = None       # 由 core.start_app 设置，供 call_soon_threadsafe

    # ---------- 读 ----------

    def get_card(self, rollcall_id):
        """按 id 取卡片（不存在返回 None）。id 兼容 int/str。"""
        with self._lock:
            return self.cards.get(str(rollcall_id))

    def snapshot(self):
        """给 UI /api/state 的公开快照（纯 JSON 可序列化）。"""
        with self._lock:
            cards = sorted(
                self.cards.values(),
                key=lambda c: c.get("detected_at", 0),
                reverse=True,
            )
            return {
                "system": {
                    "account_name": self.account_name,
                    "push_enabled": self.push_enabled,
                    "push_connected": self.push_connected,
                    "poll_interval": self.poll_interval,
                    "query_count": self.query_count,
                    "running_seconds": int(time.time() - self.start_time),
                    "version": __version__,
                },
                "cards": [dict(c) for c in cards],
                "history": [dict(e) for e in self.history],
            }

    # ---------- 写（仅 core 调用） ----------

    def register_card(self, rc, kind, type_label):
        """注册新签到卡片。返回是否成功注册。

        去重：id 已在 cards / handled_ids 中、或签到已过期 → False。
        rc 为 extract_rollcalls 产出的签到 dict。
        """
        rid = str(rc.get("rollcall_id") or "")
        if not rid:
            return False
        with self._lock:
            if rid in self.cards or rid in self.handled_ids:
                return False
            if rc.get("is_expired"):
                return False
            self.cards[rid] = {
                "rollcall_id": rid,
                "course_title": rc.get("course_title", ""),
                "created_by_name": rc.get("created_by_name", ""),
                "department_name": rc.get("department_name", ""),
                "kind": kind,
                "type_label": type_label,
                "status": "pending",
                "code_status": "fetching" if kind == "number" else "-",
                "code": None,
                "scan_url": None,
                "signed_count": None,
                "detected_at": time.time(),
                "result": None,
            }
            return True

    def update_card(self, rollcall_id, **fields):
        """更新卡片字段（未知 id 静默忽略）。"""
        with self._lock:
            card = self.cards.get(str(rollcall_id))
            if card is not None:
                card.update(fields)

    def pending_ids(self):
        """返回所有待人工确认的卡片 id（实时人数任务用）"""
        with self._lock:
            return [
                rid for rid, c in self.cards.items()
                if c.get("status") == "pending"
            ]

    def mark_terminal(self, rollcall_id, status, ok, code="", detail="", log_type=None):
        """把卡片置为终态，并追加一条历史记录（内存侧）。

        文件侧持久化由 core 另行调用 logger.log_rollcall 完成。
        """
        rid = str(rollcall_id)
        with self._lock:
            card = self.cards.get(rid)
            if card is None:
                return
            card["status"] = status
            card["result"] = {
                "ok": ok,
                "code": code,
                "detail": detail,
                "time": time.strftime("%H:%M:%S", time.localtime()),
            }
            self.history.insert(0, {
                "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                "timestamp": time.time(),
                "course": card["course_title"],
                "type": log_type or card["type_label"],
                "status": "success" if ok else "failure",
                "code": code,
                "detail": detail,
            })

    def dismiss_card(self, rollcall_id):
        """移除卡片（终态卡片的『关闭』按钮），id 进入去重表。"""
        rid = str(rollcall_id)
        with self._lock:
            if rid in self.cards:
                del self.cards[rid]
                self.handled_ids[rid] = time.time()

    def prune(self):
        """清理：终态卡片超时移入去重表；去重表超时删除。"""
        now = time.time()
        with self._lock:
            for rid, card in list(self.cards.items()):
                if card.get("status") in TERMINAL_STATUSES:
                    if now - card.get("detected_at", now) > TERMINAL_CARD_TTL:
                        del self.cards[rid]
                        self.handled_ids[rid] = now
            self.handled_ids = {
                rid: ts for rid, ts in self.handled_ids.items()
                if now - ts <= HANDLED_ID_TTL
            }
