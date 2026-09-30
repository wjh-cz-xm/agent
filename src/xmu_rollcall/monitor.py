"""Read-only cloud monitor, independent of the interactive/sign-in application.

After authenticating, the only HTTP resources read are the user's profile and
active rollcall list. Cookies and passwords stay in memory. No attendance is
submitted, no teacher detail endpoint is queried, and no external alerts are sent.
"""

import asyncio
import json
import logging
import os
import signal
import ssl
import tempfile
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.parse import quote

import aiohttp
import click
import requests

from ._xmulogin.core import _login_tronclass
from .push import build_push_url, is_rollcall_event, parse_push_frame
from .rollcall_handler import classify, type_label
from .utils import base_url, headers


def utc_now():
    return datetime.now(timezone.utc).isoformat()


class AuthenticationRequired(Exception):
    pass


class MonitorError(Exception):
    """Only fixed, credential-free error codes may be used as the message."""


class CloudSession(requests.Session):
    def request(self, method, url, **kwargs):
        kwargs.setdefault("timeout", (10, 25))
        # requests natively honors REQUESTS_CA_BUNDLE and proxy variables.
        if os.environ.get("SSL_CERT_FILE") and not os.environ.get("REQUESTS_CA_BUNDLE"):
            kwargs.setdefault("verify", os.environ["SSL_CERT_FILE"])
        return super().request(method, url, **kwargs)


def get_json(session, path):
    response = session.get(base_url + path, headers=headers, timeout=(10, 25))
    if response.status_code == 401:
        raise AuthenticationRequired()
    if response.status_code == 403:
        raise MonitorError("access_denied")
    if response.status_code != 200:
        raise MonitorError("http_" + str(response.status_code))
    try:
        data = response.json()
    except ValueError:
        raise MonitorError("invalid_json") from None
    if not isinstance(data, dict):
        raise MonitorError("invalid_response")
    return data, response.headers.get("X-SESSION-ID")


def authenticate(username, password):
    session = _login_tronclass(
        username, password, session_factory=CloudSession, quiet=True,
    )
    if session is None:
        raise MonitorError("login_failed")
    try:
        profile, session_id = get_json(session, "/api/profile")
        if profile.get("id") is None:
            raise MonitorError("profile_unavailable")
        if session_id:
            session.headers["X-SESSION-ID"] = session_id
        return session, profile["id"], session_id
    except BaseException:
        session.close()
        raise


def normalize_rollcalls(data):
    rows = data.get("rollcalls")
    if not isinstance(rows, list):
        raise MonitorError("invalid_rollcall_list")
    result = {}
    for row in rows:
        if not isinstance(row, dict) or row.get("rollcall_id") is None:
            raise MonitorError("invalid_rollcall")
        # Persist only the fields useful to the account owner, never raw payloads.
        rid = str(row["rollcall_id"])
        kind = classify(row)
        result[rid] = {
            "rollcall_id": rid,
            "course_title": row.get("course_title", ""),
            "created_by_name": row.get("created_by_name", ""),
            "kind": kind,
            "type_label": type_label(kind),
            "status": row.get("status", ""),
            "is_expired": bool(row.get("is_expired", False)),
            "rollcall_status": row.get("rollcall_status", ""),
        }
    return result


class MonitorStore:
    def __init__(self, directory, emit=None):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "state.json"
        self.records = {}
        try:
            previous = json.loads(self.path.read_text(encoding="utf-8"))
            records = previous.get("rollcalls", {})
            if isinstance(records, dict) and all(isinstance(r, dict) for r in records.values()):
                self.records = records
        except (OSError, ValueError, AttributeError):
            pass
        self.status = {
            "mode": "read_only", "started_at": utc_now(),
            "healthy": False, "last_success_at": None,
            "error": "starting", "push": "disconnected",
        }
        self.logger = None
        if emit is None:
            self.logger = logging.Logger("xmu-monitor-events")
            handler = RotatingFileHandler(
                self.directory / "events.jsonl", maxBytes=1_000_000,
                backupCount=3, encoding="utf-8",
            )
            handler.setFormatter(logging.Formatter("%(message)s"))
            self.logger.addHandler(handler)
        self.emit_callback = emit
        self.save()

    def emit(self, event, **fields):
        record = {"time": utc_now(), "event": event, **fields}
        if self.emit_callback:
            self.emit_callback(record)
        else:
            line = json.dumps(record, ensure_ascii=False)
            self.logger.info(line)
            click.echo(line)

    def save(self):
        data = {"system": {**self.status, "updated_at": utc_now()}, "rollcalls": self.records}
        fd, temporary = tempfile.mkstemp(prefix="state-", suffix=".tmp", dir=self.directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(data, stream, ensure_ascii=False, indent=2)
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def update(self, data):
        current = normalize_rollcalls(data)
        for rid, row in current.items():
            previous = self.records.get(rid)
            if previous is None:
                if not row["is_expired"] and row["status"] == "absent":
                    self.emit("new_rollcall", rollcall=row)
            elif previous != row:
                self.emit("rollcall_changed", rollcall=row)
        for rid in self.records.keys() - current.keys():
            self.emit("rollcall_removed", rollcall_id=rid)
        self.records = current
        if self.status["error"] not in (None, "starting"):
            self.emit("monitor_recovered")
        self.status.update(healthy=True, error=None, last_success_at=utc_now())
        self.save()

    def fail(self, code):
        if self.status["error"] != code:
            self.emit("monitor_error", error=code)
        self.status.update(healthy=False, error=code)
        self.save()

    def close(self):
        self.status.update(healthy=False, error="stopped", push="disconnected")
        self.save()
        if self.logger:
            for handler in self.logger.handlers:
                handler.close()


def error_code(error):
    if isinstance(error, MonitorError):
        return str(error)
    if isinstance(error, AuthenticationRequired):
        return "authentication_required"
    if isinstance(error, requests.Timeout):
        return "request_timeout"
    # Exception messages can contain credentials/redirect URLs. Do not log them.
    return "request_failed"


class Monitor:
    def __init__(self, store, username, password, interval=15, use_push=True):
        self.store = store
        self.username, self.password = username, password
        self.interval, self.use_push = interval, use_push
        self.session = None
        self.user_id = self.session_id = None
        self.wake = asyncio.Event()
        self.stopped = asyncio.Event()
        self.push_task = None
        self.auth_failures = 0

    async def disconnect(self):
        if self.push_task:
            self.push_task.cancel()
            await asyncio.gather(self.push_task, return_exceptions=True)
            self.push_task = None
        if self.session:
            self.session.close()
        self.session = None
        self.session_id = None

    def start_push(self):
        if self.use_push and self.session_id and self.push_task is None:
            self.push_task = asyncio.create_task(self.listen())

    async def check(self):
        if self.session is None:
            self.session, self.user_id, self.session_id = await asyncio.to_thread(
                authenticate, self.username, self.password,
            )
            self.auth_failures = 0
            self.start_push()
        data, session_id = await asyncio.to_thread(get_json, self.session, "/api/radar/rollcalls")
        if not self.session_id and session_id:
            self.session_id = session_id
            self.session.headers["X-SESSION-ID"] = session_id
            self.start_push()
        self.store.update(data)

    async def listen(self):
        context = ssl.create_default_context(
            cafile=os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE"),
        )
        while not self.stopped.is_set():
            try:
                async with aiohttp.ClientSession(
                    trust_env=True, timeout=aiohttp.ClientTimeout(total=30),
                    cookies=requests.utils.dict_from_cookiejar(self.session.cookies),
                ) as client:
                    # aiohttp distinguishes WSS_PROXY from HTTPS_PROXY.
                    proxy = (os.environ.get("WSS_PROXY") or os.environ.get("wss_proxy")
                             or requests.utils.get_environ_proxies(base_url).get("https"))
                    url = build_push_url(quote(str(self.user_id), safe=""),
                                         quote(self.session_id, safe=""))
                    async with client.ws_connect(
                        url, headers={"X-SESSION-ID": self.session_id},
                        heartbeat=None, ssl=context, proxy=proxy,
                    ) as socket:
                        if self.store.status["push"] == "fallback_polling":
                            self.store.emit("push_recovered")
                        self.store.status["push"] = "connected"
                        self.store.save()
                        self.wake.set()
                        async for frame in socket:
                            if frame.type == aiohttp.WSMsgType.TEXT:
                                message = parse_push_frame(frame.data)
                                if message and is_rollcall_event(message):
                                    self.wake.set()
                            elif frame.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                break
            except asyncio.CancelledError:
                raise
            except Exception:
                pass  # Polling is the fallback; never log the secret-bearing WS URL.
            if self.store.status["push"] != "fallback_polling":
                self.store.emit("push_unavailable", fallback="polling")
            self.store.status["push"] = "fallback_polling"
            self.store.save()
            try:
                await asyncio.wait_for(self.stopped.wait(), timeout=30)
            except asyncio.TimeoutError:
                pass

    async def run(self, once=False):
        try:
            while not self.stopped.is_set():
                self.wake.clear()
                delay = self.interval
                try:
                    await self.check()
                except AuthenticationRequired as error:
                    self.store.fail(error_code(error))
                    await self.disconnect()
                    delay = 60
                except Exception as error:
                    self.store.fail(error_code(error))
                    delay = max(60, self.interval)
                    if self.session is None:
                        self.auth_failures += 1
                        delay = min(900, 60 * 2 ** min(self.auth_failures - 1, 4))
                if once:
                    return self.store.status["healthy"]
                # Wake coalesces duplicate push messages, avoiding concurrent polls.
                waiter = asyncio.create_task(self.wake.wait())
                stopper = asyncio.create_task(self.stopped.wait())
                try:
                    await asyncio.wait([waiter, stopper], timeout=delay,
                                       return_when=asyncio.FIRST_COMPLETED)
                finally:
                    waiter.cancel()
                    stopper.cancel()
                    await asyncio.gather(waiter, stopper, return_exceptions=True)
                # Bound repeated event-driven requests to at most one per second.
                if self.wake.is_set() and not self.stopped.is_set():
                    await asyncio.sleep(1)
        finally:
            await self.disconnect()


async def run_monitor(monitor, once):
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, monitor.stopped.set)
        except (NotImplementedError, RuntimeError):
            pass  # Windows: asyncio.run handles Ctrl+C.
    return await monitor.run(once)


@click.command()
@click.option("--interval", type=click.IntRange(min=5), default=15, show_default=True,
              help="Fallback polling interval in seconds.")
@click.option("--state-dir", type=click.Path(path_type=Path), default=".monitor",
              envvar="XMU_MONITOR_STATE_DIR", show_default=True)
@click.option("--poll-only", is_flag=True, help="Disable the optional WebSocket listener.")
@click.option("--once", is_flag=True, help="Check once and exit; useful for verification.")
def main(interval, state_dir, poll_only, once):
    """Monitor your TronClass rollcalls without submitting attendance.

    Set XMU_USERNAME and XMU_PASSWORD in the environment's personal secret store.
    Credentials are never taken from the original application's local config.
    """
    username, password = os.environ.get("XMU_USERNAME"), os.environ.get("XMU_PASSWORD")
    if not username or not password:
        raise click.ClickException("Set XMU_USERNAME and XMU_PASSWORD using personal environment variables.")
    store = MonitorStore(state_dir)
    monitor = Monitor(store, username, password, interval, not poll_only and not once)
    try:
        ok = asyncio.run(run_monitor(monitor, once))
        if once and not ok:
            raise click.ClickException("Monitor check failed; see state.json for the credential-free error code.")
    except KeyboardInterrupt:
        pass
    finally:
        store.close()


if __name__ == "__main__":
    main()
