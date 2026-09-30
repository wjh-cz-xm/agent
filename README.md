# xmu-rollcall

厦门大学畅课（TronClass）自动签到工具：Web UI + 推送监听 + 人工确认签到。

v5.0.0 独立维护版。基于上游 [KrsMt-0113/XMU-Rollcall-Bot](https://github.com/KrsMt-0113/XMU-Rollcall-Bot)（MIT）延续开发，登录 SDK `xmulogin` 已内置（vendored）。

## 云端只读监控（新增）

使用独立的 `xmu-monitor` 命令监听本人签到变化，默认推送 + 15 秒轮询，
仅记录日志和状态，不外发消息、不提交签到。支持云环境代理、个人环境变量、
会话失效重登录、事件去重和日志轮换。
安装、个人凭据配置、启动步骤及持续运行边界见 [云端监控说明](docs/cloud-monitor.md)。
下面的 `xmu start` 属于原交互式签到应用，云端只读监控无需启动它。

## 功能

- **推送监听**：ntf pubsub WebSocket 秒级感知新签到（需要 `X-SESSION-ID`）；轮询降为兜底（默认 15s，可配置）
- **Web UI**：黑灰极简仪表盘（`http://127.0.0.1:5000`），显示正在签到的课程卡片 + 历史签到记录
- **人工确认签到**：检测到签到后 UI 弹出卡片，实时显示已签到人数；**点『签到』才执行**，不再全自动
- **数字码签到**：检测时即从教师端详情接口**直接取码**（UI 显示四位码 / 取码中 / 取码失败）；点『签到』后已知码单次提交，取码失败自动回退 0-9999 暴力枚举
- **雷达签到**：点『签到』后双点定位 + 圆交点反推坐标提交
- **二维码签到**：维持全自动（检测即发起 ngrok 扫码，扫码链接显示在 UI 卡片，可复制到手机）
- **多账号**：账号增删切换，session cookie 本地缓存

v5 变更：移除全自动签到与 `wait_before_answer`（改为 UI 实时人数 + 人工判断）、移除 PushPlus 微信推送（结果在 UI 直接可见）、控制台仪表盘由 Web UI 取代。

## 安装

要求 Python >= 3.10。本机开发安装（推荐，改动即时生效）：

```powershell
py -m pip install -e "D:\projects\xmu-rollcall[qr]"
```

> ⚠️ 本机存在 Anaconda 时，裸 `python`/`pip` 可能指向 Anaconda 环境，
> 请始终使用 `py` 启动器或完整路径的 Python。
>
> ⚠️ 请勿从 PyPI 安装 `xmu-rollcall-cli`（原作者的旧包名），本项目只从源码安装。

## 配置

```powershell
xmu config   # 交互式配置：账号 / ngrok token / 兜底轮询间隔
xmu switch   # 切换当前账号
```

配置目录解析顺序：环境变量 `XMU_ROLLCALL_CONFIG_DIR` → `~\.xmu_rollcall` → 当前目录 `.xmu_rollcall`。

**⚠️ 密码以明文存储在 config.json 中**（沿用上游行为，改进见 development.md）。

## 使用

```powershell
xmu start              # 启动：恢复会话 → 打开浏览器进入仪表盘（Ctrl+C 退出）
xmu start --no-browser # 同上，但不自动打开浏览器（无头/看门狗场景）
xmu demo               # 演示模式：模拟一次数字码签到预览 UI（不联网、不写真实数据）
xmu refresh            # ⚠️ 删除当前账号的登录缓存（cookie + 会话元数据），下次启动需重新登录
xmu --help
```

启动后浏览器打开 `http://127.0.0.1:5000`。新签到出现时页面弹出卡片：

- 课程名、教师、类型、**实时已签人数**（每 3s 刷新）
- 数字码：后台自动取码，显示四位码（取码失败则提示将暴力枚举）
- 点击 **签到** 按钮执行；结果与历史记录即时显示

没有真实签到时可用 `xmu demo` 体验完整 UI 流程：模拟一张数字码卡片
（2s 出码、人数每 3s +1），点「签到」模拟提交成功，全程不联网、
历史只进内存不写日志文件。

## 推送通道

登录后会从 `/api/profile` 响应头捕获 `X-SESSION-ID` 并缓存到配置目录
（`{账号id}.meta.json`，随 `xmu refresh` 一起删除）。推送通道（ntf pubsub
WebSocket）据此建立长连接；连接失败自动 5s 重连，期间轮询兜底保证不漏签。
若服务器未下发该头，程序自动降级为纯轮询（console 会提示）。

## 看门狗（可选）

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\watchdog.ps1
```

脚本自动解析 `xmu.exe` 位置（PATH → Python314 Scripts → py 启动器），进程退出 10 秒后自动拉起（带 `--no-browser`）。开机自启：把上述命令建成 `shell:startup` 里的快捷方式即可。

## 作为库 / AI Agent 集成

CLI 是薄封装，核心逻辑可直接 import：

```python
from xmu_rollcall._xmulogin import xmulogin        # 内置 CAS 登录 SDK
from xmu_rollcall.verify import get_number_code, answer_number_code_async, get_signed_count
from xmu_rollcall.state import AppState
from xmu_rollcall.core import start_app            # 完整启动（UI + 推送 + 人工确认）

start_app(account_dict, open_browser=False)        # Agent 场景：无浏览器运行

# 或只取数据：
session = xmulogin(type=3, username=acct["username"], password=acct["password"])
code = get_number_code(session, 12345)             # 直接取签到码
count, _ = get_signed_count(session, 12345)        # 已签人数
```

注意：`verify.send_code`（同步版）内部用 `asyncio.run`，**不得在已有运行中
事件循环的线程调用**；异步场景请用 `answer_number_code_async`。

AI Agent 场景建议通过库调用而非子进程；`xmu start --no-browser` + 轮询
`http://127.0.0.1:5000/api/state` 也可作为进程外的状态读取面。

## 项目结构

```
src/xmu_rollcall/
├── cli.py                # Click CLI 入口（config/start/switch/refresh）
├── core.py               # v5 编排核心：事件循环、签到注册/执行、人工确认
├── state.py              # 共享状态（AppState：卡片/历史/去重）
├── push.py               # ntf pubsub 推送监听（Atmosphere 帧解析）
├── ui.py                 # Web UI（Flask，/api/state /api/answer /api/dismiss）
├── rollcall_handler.py   # 签到数据提取与类型判定（纯逻辑）
├── verify.py             # 取码、人数统计、单码提交、暴力枚举、雷达定位
├── qr_handler.py         # 二维码签到（Flask + ngrok + 手机扫码）
├── config.py             # 配置目录/账号/设置/会话 meta 路径
├── logger.py             # 签到日志（JSON，100 条滚动）
├── parse_code.py         # 二维码 payload 解码（base36 编码）
├── utils.py              # session 与 X-SESSION-ID 元数据存取
├── _xmulogin/            # 内置 CAS 登录 SDK（vendored，MIT）
└── templates/
    ├── dashboard.html    # 仪表盘单页（黑灰）
    └── scan.html         # 手机扫码页
tests/                    # pytest 离线测试
scripts/watchdog.ps1      # 看门狗脚本
docs/                     # API 盘点与开发笔记
```

## 开发

```powershell
py -m pip install pytest
py -m pytest          # 全部离线测试，绝不触碰真实配置目录
```

## 免责声明

本项目仅供学习研究。自动签到有违教学秩序的风险，使用后果自负；请遵守学校规定。

## 致谢

- [KrsMt-0113/XMU-Rollcall-Bot](https://github.com/KrsMt-0113/XMU-Rollcall-Bot) — 上游项目（MIT）
- [YixuAnsensei/xmu_rollcall_zako_Tronclass](https://github.com/YixuAnsensei/xmu_rollcall_zako_Tronclass) — 直接取码思路
- [wilinz/fuck_tronclass_sign](https://github.com/wilinz/fuck_tronclass_sign) — 二维码 payload 解码
- [alkali210/XMU-Rollcall-Bot](https://github.com/alkali210/XMU-Rollcall-Bot) — 思路参考
- `xmulogin` SDK（MIT，已内置）
