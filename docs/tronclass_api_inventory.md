# TronClass（畅课）API 盘点与价值清单

> 整理时间：2026-09-03。来源：上游仓库自带 SDK（`xmu-tronclass-sdk`）、tronclass-dida-sync 项目、
> zako 项目、alkali210 fork、seven-317/Tronclass-API（TS 实现，可对照）、实际逆向验证。
> 所有接口的认证前提相同：合法 session cookie + `X-SESSION-ID` header。

## 1. 通用约定

- **Base URL**：厦大实例 `https://lnt.xmu.edu.cn`；接口面为畅课通用设计，其他学校实例（见 §12）大概率兼容
- **认证**：
  - CAS SSO 登录后访问 `/api/login?login=access_token`（OIDC code → access_token → 最终 session cookie），详见 `src/xmu_rollcall/_xmulogin/core.py`
  - **`X-SESSION-ID`**：从 `/api/profile` 的**响应头**提取（登录响应中不提供），挂到 session.headers 并缓存到 `{账号id}.meta.json`。✅ v5 已实现（`utils.fetch_profile_meta` + `core.start_app` 绑定 + 轮询响应头机会式补捕）
  - 部分接口带 `Referer: https://lnt.xmu.edu.cn/` 可提高成功率（已在 verify.py 中采用）
- **请求头**：`Accept: application/json, text/plain, */*` + `Accept-Language: zh-CN,zh;q=0.9`
- **状态码语义**（SDK 约定）：401 会话过期 / 403 无权限 / 404 资源不存在
- **SDK 参考实现**：`D:\大学\tronclass\XMU-Rollcall-Bot-main\XMU-Rollcall-Bot-main\xmu-tronclass-sdk\tronclass\api\`（未移植，作为接口资料库用）

## 2. 签到 / 点名

### 已在 xmu-rollcall 中使用的

| 接口 | 方法 | 用途 |
|---|---|---|
| `/api/radar/rollcalls` | GET | 轮询当前进行中的签到列表（含数字码/雷达/二维码类型） |
| `/api/rollcall/{rid}/student_rollcalls` | GET | 教师端详情（权限审查不严）：直接读 `number_code`、统计已签人数 |
| `/api/rollcall/{rid}/answer_number_rollcall` | PUT | 数字码提交 `{deviceId, numberCode}` |
| `/api/rollcall/{rid}/answer` | PUT | 雷达坐标提交（返回 `distance` 供三角定位） |
| `/api/rollcall/{rid}/answer_qr_rollcall` | PUT | 二维码 payload 提交 |

### 尚未使用的补充端点

| 接口 | 方法 | 用途 / 价值 |
|---|---|---|
| `/api/rollcall/{rid}` | GET | 单个签到完整详情 |
| `/api/rollcall/merged-rollcall` | GET | 合并考勤视图 |
| `/api/rollcall/merged-rollcall/{rid}/student-rollcalls` | GET | 合并考勤的学生记录 → **个人考勤统计面板** |
| `/api/courses/rollcall_status/{course_id}` | GET | 单门课的签到状态 → 缺勤统计 |
| Socket.IO `self_registration_rollcall_start` | 事件 | 自主注册型签到（一种没在轮询里出现的签到类型！见 §3） |

## 3. 实时推送通道（3.1 已在 v5 实现）

三条并行通道，新签到事件**秒级送达**，可把轮询降级为兜底：

### 3.1 ntf pubsub WebSocket（✅ 已实现：src/xmu_rollcall/push.py）

```
wss://lnt.xmu.edu.cn/ntf/pubsub/{user_id}
    ?X-Atmosphere-tracking-id=0
    &X-Atmosphere-Transport=websocket
    &Content-Type=application%2Fjson
    &X-atmo-protocol=true
    &X-SESSION-ID={session_id}
```

- Atmosphere 协议：消息格式 `xxx|yyy|zzz|{json}`，取第 4 段 JSON
- 消息 `type` 含 `rollcall` 即签到事件；其余为通用通知
- `user_id` 从 `/api/profile` 的 `id` 字段获取
- v5 实现要点：aiohttp 客户端（`heartbeat=None`，Atmosphere 不应答 WS ping）、
  推送事件只作 wake-up（`core.check_now` 拉取规范数据 + rollcall_id 去重）、
  断线 5s 重连、轮询兜底 15s、X-SESSION-ID 缺失时每 30s 重查（轮询响应头
  机会式补捕自愈）

### 3.2 Socket.IO `/schoolTimeTable` 命名空间

- `sio.connect(base_url, namespaces=["/schoolTimeTable"], headers={"X-SESSION-ID": sid}, transports=["websocket"])`
- 事件：`self_registration_rollcall_start` — 自主注册型签到（如扫码自助登记）

### 3.3 FCM/MCS 长连接（最全，最重）

- 注册链：`android.clients.google.com/checkin`（protobuf）→ `/c2dm/register3`（拿 FCM token）→ OneSignal `POST /api/v1/players`（app_id `c810be65-8ec7-4f73-a802-20862e93c9b8`）→ 更新 player 的 `external_user_id`（`XMU_user_{uid}_lang_en_us`）+ tags
- 长连接 `mtalk.google.com:5228`（TLS，MCS 二进制协议，自实现最小 protobuf 编解码）
- 推送 `custom.a.message ∈ {NUMBER_ROLLCALL, RADAR_ROLLCALL, QRCODE_ROLLCALL}` 即签到事件
- 状态持久化 `~/.tronclass_fcm_state.json`
- 优点：官方主通道、覆盖最全；缺点：依赖 Google 服务器可达性 + 协议复杂

### 落地建议

1. 登录流程中提取并缓存 `X-SESSION-ID`（cookie 旁路存 config 目录）
2. 实现 3.1 ntf WebSocket 监听器（`websockets` 库即可，无需新依赖的大头）
3. 事件回调直接复用现有 `process_rollcalls` 逻辑；轮询保留为兜底（间隔可调大，省请求）

## 4. 作业 / 考试 / 问卷

| 接口 | 方法 | 说明 |
|---|---|---|
| `/api/todos` | GET | 待办聚合，`todo_list` 数组；类型 `homework/questionnaire/exam/exercise`，字段含 `end_time/is_locked` |
| `/api/homeworks` | GET | 作业列表（分页参数） |
| `/api/homework/{hid}` | GET | 作业详情 |
| `/api/submissions/` | GET/POST | 提交记录列表 / 提交作业（POST body 含 `homework_id`） |
| `/api/submission/{sid}` | GET | 单条提交详情 |
| `/api/exams/`、`/api/exam/{eid}` | GET | 考试列表 / 详情 |
| `/api/exams/submissions/{eid}` | GET | 考试提交 |
| `/api/questionnaires/`、`/api/questionnaire/{qid}` | GET | 问卷列表 / 详情 |
| `/api/feedbacks/` | GET | 教学反馈问卷 |
| `/api/uploads/document/` `/video/` `/audio/` | POST | 作业附件上传 |

**价值**：作业 DDL 监控/秒级提醒（tronclass-dida-sync 已有 `/api/todos` 打底，可升级为推送驱动）；问卷自动填（与签到自动化同思路，注意合规）。

## 5. 课程 / 课件

| 接口 | 方法 | 说明 |
|---|---|---|
| `/api/my-courses` | POST | 课程列表；body：`{conditions: {semester_id, academic_year_id, keyword, classify_type}, fields, page, page_size}` |
| `/api/my-semesters` | GET | 学期列表 |
| `/api/course/{cid}` | GET | 课程详情 |
| `/api/courses/{cid}/activities` | GET | 课程活动 |
| `/api/courses/{cid}/bulletins` | GET | 课程公告 |
| `/api/course/{cid}/coursewares` | GET | 课件列表 |
| `/api/activities/{aid}/upload_references` | GET | 课件/活动附件引用 |
| `/api/uploads/reference/document/{fid}/url?preview=true` | GET | **课件文件直链下载** |
| `/api/syllabus/{cid}` | GET | 教学大纲 |
| `/api/modules/{cid}` | GET | 章节/模块 |
| `/api/groups/{cid}` | GET | 分组 |
| `/api/enrollment/{cid}` | GET | 选课名单 |
| `/api/courses/interactions/{cid}`、`.../{cid}/{iid}` | GET | 课堂互动（投票/测验类） |
| `/api/courses/public?page=&keywords=` | GET | 公开课检索 |
| `/api/course/enrollments/join/` | POST | 邀请码加课 `{code}` |

**价值**：课件批量下载器 → RAG 语料（对接 chem-rag / 化学知识库项目）。

## 6. 活动 / 课件库

| 接口 | 方法 | 说明 |
|---|---|---|
| `/api/activities/{aid}` | GET | 活动详情 |
| `/api/user/courses/activities` | GET | 用户课程活动 |
| `/api/course/activity-read/{cid}` | GET | 标记活动已读 |
| `/api/courseware-quiz/activity/{aid}` | GET | 课件测验 |
| `/api/online-videos/{aid}` | GET | 在线视频 |
| `/api/courses/lecture-live-activity/` | GET | 直播课活动 |
| `/api/public-lives` | GET | 公开直播 |
| `/api/shared-resources/` | GET | 共享资源 |
| `/api/notebooks/` | GET | 笔记 |

## 7. 论坛

| 接口 | 方法 | 说明 |
|---|---|---|
| `/api/topics` | GET/POST | 主题帖列表 / 发帖 |
| `/api/topics/{tid}` | GET | 帖子详情 |
| `/api/topics/topped?course_id=` | GET | 置顶帖 |
| `/api/replies/` | POST | 回复 |
| `/api/replies/{rid}` | DELETE | 删除回复 |
| `/api/forum/categories/{cid}` | GET | 版块分类 |
| `/api/courses/ask-questions/` | GET/POST | 课程问答 |

**价值**：自动回复机器人、课程答疑监控（喂给 AI Agent 的语料入口之一）。

## 8. 通知

| 接口 | 方法 | 说明 |
|---|---|---|
| `/api/todos` | GET | 待办（同 §4） |
| `/api/alert/messages/read` | POST | 标记消息已读（body 含消息 id 数组） |
| `/api/org-bulletin/bulletins?page=` | GET | 校级公告列表 |
| `/api/org-bulletin/bulletins/{bid}` | GET | 公告详情 |
| `/api/bulletins/` | GET | 公告（分页） |

## 9. 个人资料 / 用户

| 接口 | 方法 | 说明 |
|---|---|---|
| `/api/profile` | GET | 个人资料（含 `id` → user_id、`name`） |
| `/api/user/name` `/email` `/mobile-phone` `/password` | PUT | 资料修改（有风险接口，仅记录） |
| `/api/user/avatar` | POST | 头像上传 |
| `/api/user/tags` | GET | 用户标签（OneSignal 推送订阅用，§3.3 依赖） |
| `/api/user/ntf-setting` | GET/PUT | 通知设置 |
| `/api/user/bound-services` | GET | 绑定服务 |
| `/api/user/recently-visited-courses` | GET | 最近访问课程 |
| `/api/user/uploads` | GET | 个人上传文件 |
| `/api/user/health-passport` | GET | 健康护照（历史功能） |

## 10. 晓懂 AI（已在 LLM-2 集成）

| 接口 | 方法 | 说明 |
|---|---|---|
| `/api/air-credit/course/{cid}/token` | GET | 课程额度 token |
| `/api/air-credit/user/token` | GET | 用户额度 token |
| `/api/air-credit/course/{cid}/chat-usage-info` | GET | 聊天用量信息 |
| `/api/air-agent/recent-conversations` | GET | 晓懂最近会话 |

实现参考：`D:\projects\tronclass-dida-sync\xiaodong_api.py`（完整客户端）；代理服务已整合进 LLM-2（`services\xiaodong\`）。

## 11. 登录 / OIDC 细节

- 流程：IDS CAS（AES-CBC 密码加密，salt+execution 从登录页 HTML 提取）→ OIDC authorization code（client_id `TronClassH5`，`c-identity.xmu.edu.cn`）→ `POST /api/login?login=access_token`（`{access_token, org_id: 1}`）→ session cookie + **`X-SESSION-ID`**
- **验证码（风控触发，非常态）**：IDS 登录页 HTML 含 `needCaptcha` 变量，为 `true` 时自动登录必失败。正常使用不会出现；异常行为（短时间频繁自动登录、可疑 UA/IP）会触发，同类先例见 IDS CAS 限流（20+ 次登录/小时被临时拒）。应对：cookie 缓存优先复用（本项目与 dida-sync 的缓存策略均基于此），触发后等几小时或用浏览器手动登录建立信任会话
- 全流程参考：`tronclass-dida-sync/tronclass_api.py` 的 `_login_tronclass()`（注释最全的参考实现，含 needCaptcha 防御性检查）

## 12. 全平台实例枚举

`https://api-org.tronclass.com.cn/orgs?keywords={字符}`（需 `x-lc-id`/`x-lc-key`，见上游 `Tronclass-URL-list/main.py`）可枚举所有部署实例（orgName + apiUrl），成果 CSV 在 `D:\大学\tronclass\XMU-Rollcall-Bot-main\XMU-Rollcall-Bot-main\Tronclass-URL-list\result.csv`。说明接口面跨校通用，工具可移植到其他畅课学校。

## 13. 落地路线图（结合现有项目）

| 优先级 | 事项 | 涉及接口 | 备注 |
|---|---|---|---|
| ✅ v5 完成 | 推送监听器进 xmu-rollcall | §3.1 ntf WS + `X-SESSION-ID` 提取 | push.py + core.py，轮询降为兜底，人工确认 UI |
| ★★★ | 作业 DDL 监控升级 | `/api/todos` + 推送 | dida-sync 打底，改推送驱动 |
| ★★ | 课件下载器 | §5 直链接口 | RAG 语料，对接 chem-rag |
| ★★ | 个人考勤面板 | merged-rollcall 系列 | 统计/可视化 |
| ★ | 课程答疑/公告监听 | §7/§8 | Agent 语料 |
| 备查 | 加密凭证存储 | — | 参考 alkali210 `secure_store.py`（需修正其设计缺陷，见 development.md） |

## 14. 参考资料

- 本地 SDK 源码（接口资料库）：`D:\大学\tronclass\XMU-Rollcall-Bot-main\XMU-Rollcall-Bot-main\xmu-tronclass-sdk\`
- 上游仓库：https://github.com/KrsMt-0113/XMU-Rollcall-Bot
- TS 非官方库（验证码 OCR、错误处理可对照）：https://github.com/seven-317/Tronclass-API
- 登录逆向细节：https://github.com/ogios/TronclassLogin_eurasia
- 直接取码思路：https://github.com/YixuAnsensei/xmu_rollcall_zako_Tronclass
- wait_before_answer 等思路：https://github.com/alkali210/XMU-Rollcall-Bot
- 二维码 payload 解码：https://github.com/wilinz/fuck_tronclass_sign
