# 云端只读监控

此仓库从本机 `xmu-rollcall` 的工作副本导入，包含已有的未提交功能修改。
原来的交互式 `xmu start` 仍保留。云端监控使用独立命令 `xmu-monitor`。

## 能监控什么

- 已登录账号可见的签到开始、类型、课程、教师、本人签到状态和列表变化。
- WebSocket 收到签到事件后立即刷新，另以默认 15 秒轮询兜底。
- `events.jsonl` 只记录变化、故障和恢复；无变化时不刷屏。
- `state.json` 每次检查更新，包含 `last_success_at` 和健康状态。
- 会话返回 401 后重新登录；失败逐步退避，避免反复快速尝试登录。
- 不外发通知，不提交签到，不获取数字码，不查询其他学生记录。
- 作业、课件、成绩和课程公告的完整监控尚未接入；API 盘点不代表这些功能已实现。

推送及登录是否可用必须在实际云环境中验证。网络限制、验证码或校方风控会影响访问。
HTTPS 请求和 WebSocket 均保留云环境代理及 CA 配置，推送不可用时降级为轮询。

## 在 Codex 云环境设置中继续

仓库：`wjh-cz-xm/agent`，分支：`main`。

向设置聊天发送：

> main 分支已有 Python 项目。请阅读 docs/cloud-monitor.md，运行
> bash scripts/cloud_setup.sh 安装并执行离线测试。把该命令作为安装步骤。
> 设置阶段不运行 xmu start 或连接真实账号。配置个人环境变量需求
> XMU_USERNAME、XMU_PASSWORD；发布前保持个人值为空。
> 完成后提供可审查的配置，让我发布环境。

最低 Python 版本 3.10；建议 3.12 或 3.13。安装脚本只创建虚拟环境、安装项目并运行离线测试。
尚未在 Linux 云端实际执行安装脚本，本地测试不能替代云端验证。

## 网络和个人凭据

允许包管理器网络访问，并按实际 SSO 重定向检查所需域名，已知入口包括：

- `lnt.xmu.edu.cn`
- `c-identity.xmu.edu.cn`
- `ids.xmu.edu.cn`
- `c-mobile.xmu.edu.cn`

如重定向到其他校方域名，在确认后补充该域名；不要关闭 TLS 校验。

在 Codex Cloud 的 Personal vault / 个人凭据中添加 **Environment variable**：

- `XMU_USERNAME`：本人学号/工号。
- `XMU_PASSWORD`：本人统一身份认证密码。

程序需要读取原值后按校方协议加密，所以这两个值不能使用网络代理替换占位符类型。
只对这个环境生效，不在聊天正文、GitHub、安装脚本或环境快照中保存密码。
不要上传原应用的 config.json、Cookie 或会话缓存。本监控入口不会自动读取本机配置。

## 发布后启动和检查

发布环境，然后新建云端聊天并选择该环境。在该任务的仓库根目录先做一次只读检查：

```bash
.venv/bin/xmu-monitor --once
```

检查成功后持续运行：

```bash
.venv/bin/xmu-monitor --interval 15
```

长连接不可用时可强制仅轮询：

```bash
.venv/bin/xmu-monitor --poll-only --interval 15
```

状态与事件默认位于 `.monitor/state.json`、`.monitor/events.jsonl`。
它们已加入 Git 忽略；事件文件按 1 MB 轮换，最多三个备份。
可用 `--state-dir` 或 `XMU_MONITOR_STATE_DIR` 指定持久目录。
同一个状态目录只启动一个监控实例；切换账号时请使用另一个目录。

`--once` 会执行一次检查后退出，`system.healthy` 随退出变为 false，
但 `last_success_at` 保留，退出码 0 表示该次检查成功。
`rollcall_removed` 只表示签到不再出现在接口返回列表中，并不证明签到成功或课程结束。
历史已签到记录首次读到时只进入状态，不产生“新签到”事件。

正式任务中可发送：

> 按 docs/cloud-monitor.md 使用我的个人环境变量，先执行一次只读检查。
> 成功后启动 xmu-monitor，每 15 秒轮询兜底。仅保留日志和状态，不外发消息，
> 不执行签到。验证进程仍在运行、状态时间持续更新，并报告实际启动结果。

## 持续运行的边界

Codex 云任务可以在本机休眠时运行，但环境发布、文件快照和任务状态保留不等于
24×7 服务托管。官方文档没有承诺聊天结束后任意后台进程持续运行。
不要把 `nohup` 或设置聊天中的一次启动当作全天候监控保证。

先在任务运行期间验证监控。若需要整个学期持续值守，应把同一程序部署到
常开主机/服务器，配合服务管理器自动重启、持久化状态和健康检查；本次未部署该服务。

参考：https://learn.chatgpt.com/docs/environments/cloud-environments
