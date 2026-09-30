# 开发与维护笔记

## 接口资料

畅课 API 全景盘点（实时推送通道、作业/课件/论坛/通知等接口清单与落地路线）见
[tronclass_api_inventory.md](tronclass_api_inventory.md)。

## 版本号

唯一来源：`src/xmu_rollcall/__init__.py` 的 `__version__`。pyproject.toml 通过
`[tool.setuptools.dynamic] version = { attr = "xmu_rollcall.__version__" }` 动态读取，
CLI 横幅与 PyPI 版本检查同样引用它。发版时只改 `__init__.py`。

## v5 架构速览

- **线程模型**：主线程 = asyncio 事件循环（`core._main`）；Flask UI 与 confirm
  分发线程均为 daemon。UI 只读 state + 入队确认请求，一切业务写在 core 循环内。
- **雷区一（asyncio.run）**：`verify.send_code`（同步薄壳）内部 `asyncio.run`，
  **不得在运行中的事件循环线程调用**。core 只允许用 `answer_number_code_async`。
- **雷区二（executor 排空）**：阻塞 `queue.get` 绝不放进事件循环执行器
  （`asyncio.run` 关闭时 `shutdown_default_executor` 会永久挂起）；confirm 分发
  用独立 daemon 线程 + `loop.call_soon_threadsafe`。同理 `send_qr` 的等待循环
  必须轮询 `stop_event`（≤1s 感知退出）。
- **aiohttp 会话**：新 `ClientSession` 一律 `trust_env=False`（cli 剥环境变量
  只对 CLI 生效，库调用需自保）；WS 用 `heartbeat=None`（Atmosphere 不应答
  WS 协议 ping）。
- **X-SESSION-ID**：登录/恢复会话后从 `/api/profile` **响应头**提取（登录响应
  里没有），缓存到 `{账号id}.meta.json`；缺失时推送停用、轮询兜底，且
  `check_now` 会在轮询响应头里机会式补捕自愈。`xmu refresh` 删除 cookie 时
  需同步删 meta（已由 `perform_account_deletion` 处理）。

## 已知问题 / 后续改进

- **明文密码**：config.json 中账号密码明文存储。参考 alkali210 fork 的
  `secure_store.py`（SQLite + AES-GCM）可改进，但其实现有设计缺陷
  （无 KDF 的 SHA-256 密钥、解密失败静默吞错、无 AAD、Windows 下 chmod 无效），
  照抄前需修正。
- **暴力枚举**：10,000 并发任务的 asyncio 设计较粗暴（上游遗留），只在直接
  取码失败且用户点击后走到；万级任务在共享 loop 里瞬时占满，属可接受范围
  （Semaphore 200），暂不重构。
- **会话过期续签**：运行中会话过期后轮询持续失败（不崩 loop，只打日志），
  连续失败自动重登录尚未实现，留作后续小项。
- **xmu refresh 破坏性**：删除 cookie 缓存，冒烟测试时禁止运行。
- **PyPI 发布**：如发布需先确认 `xmu-rollcall` 包名可用（原包名
  `xmu-rollcall-cli` 归属上游作者）。`cli.py` 的版本检查对 404 静默降级。
- **上游 3.4.1 差异**：本地改动基于 3.3.1，与上游 3.4.1 未做 diff，
  值得拉一份对比看看有没有漏掉的上游修复（注意保留本地功能：直接取码、
  二维码签到、推送监听、人工确认 UI 等）。
- **已弃用功能**：PushPlus 微信推送（v5 移除，UI 直接展示结果与扫码链接；
  如需恢复可从 git 历史找回 `pushplus.py`）、`wait_before_answer`（v5 移除，
  `config.get_wait_before_answer` 保留作惰性兼容，配置键可安全忽略）。

## 代码约定

- 配置目录绝不硬编码；一律走 `config.get_config_dir()` / `CONFIG_DIR`。
- 新增 API 调用统一放 `verify.py`，请求头复用 `fetch_headers`
  （含 `Referer: lnt.xmu.edu.cn/`，提高成功率）。
- 共享状态只能通过 `state.AppState` 的方法读写（内部一把 `threading.Lock`）；
  新增状态字段时同步更新 `snapshot()` 与 `dashboard.html` 的渲染。
- 测试必须离线：用 `tests/conftest.py` 的 FakeSession/FakeResponse；
  conftest 在导入任何模块前把 `XMU_ROLLCALL_CONFIG_DIR` 指到临时目录。
  异步测试用 `asyncio.run()` 直接跑协程，或注入假 client_factory。
- 新增配置字段：默认值兜底写进 `config.py`（参考 `get_poll_interval`），
  CLI 菜单项同步加进 `cli.py` 的 config 菜单。

## Agent 集成约定

- CLI 是薄封装：`cli.py` 只做参数/交互，逻辑都在其他模块。
- 外部（如 AI Agent）应直接 import 核心函数（`core.start_app`、
  `verify.get_number_code` 等），不要通过子进程调 `xmu`。
- 无头场景：`start_app(account, open_browser=False)` 或
  `xmu start --no-browser`；状态面 `GET http://127.0.0.1:5000/api/state`。
- 登录 SDK 是私有子包 `_xmulogin`，仅供内部使用，不作为公共 API 承诺。
