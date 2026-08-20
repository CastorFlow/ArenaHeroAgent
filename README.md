# Arena Hero 自适应战术 Agent

一个面向 Arena Hero 的长期运行战术 Agent。项目使用官方 Python SDK，包含 API Key 登录初始化网页、自适应经济、动态产兵、编队推进、Core 斩首、信标控制、运行状态持久化，以及可选的 Chrome 路线与统计叠加层。

当前兼容基线：

- Arena Hero gameplay rules v0.14
- Arena Hero HTTP/WebSocket API v0.1
- `arena-hero` Python SDK `>=0.2.9,<0.3`
- Python 3.11+

本项目是社区战术实现，不是 Arena Hero 官方客户端。Agent 会真实控制账号中的单位；首次运行前请先阅读[完整用法](docs/USAGE.md)和[策略说明](docs/STRATEGY.md)。

社区交流：[LINUX DO 项目讨论帖](https://linux.do/t/topic/2703804)

## 主要能力

- 五种运行模式：发育、侵略、抢信标、迁移、闪电速攻。
- 工人按当前视野、可信历史、资源刷新分区和浏览器低置信提示分层搜索。
- 载货工人优先回仓，Core 门口自动排队与腾位，检测静止和小范围打转。
- 按实时人口调用官方 `unit_cost`，避免跨价格档位时盲目产兵。
- 先锋在前、游侠在后，主力按集结、推进、等待增援、撤退阶段协同行动。
- 暴露且无战斗护卫的敌方 Core 会触发本地 `1 先锋 + 2 游侠` 斩首队。
- 可选独立偷袭编组，保留固定家防后扩圈搜索敌方 Core。
- Core 自动治疗、修盾、产兵、靠近信标，并为回仓物流暂停迁移。
- 策略文件支持双 Tick 热加载；新版本异常时保留或回滚到旧策略。
- 本地叠加层显示路线、资源、单位、统计、中文事件和战术控制。

## Windows 快速开始

在 PowerShell 中运行：

```powershell
.\setup.ps1
.\start_all.ps1
```

浏览器会打开 `http://127.0.0.1:8766/`。在登录页输入 Arena Hero API Key；Dashboard 会通过官方 SDK 验证，只有鉴权成功并收到首个 Turn 后才启动完成、创建默认 `develop` 配置并进入控制台。API Key 不落盘。

`start_all.ps1` 还会启动只监听 `127.0.0.1:8765` 的可选叠加层桥接服务。随后在 Chrome/Edge 中：

1. 打开扩展管理页并启用开发者模式。
2. 选择“加载已解压的扩展程序”。
3. 选择仓库中的 `arena_hero_route_overlay` 目录。
4. 登录拥有该 API Key 的 Arena Hero 账号并打开 Arena 页面。

高级用法：`set_key.ps1` + `start_arena_hero.ps1` 仍可用 DPAPI 在前台直接运行 Agent，不经过网页初始化。

停止本仓库启动的 Dashboard、Agent 和叠加层：

```powershell
.\stop_all.ps1
```

Linux/VPS 部署、自定义域名、Nginx HTTPS 和无域名 SSH 隧道见[部署文档](docs/DEPLOYMENT.md)。完整参数见[使用文档](docs/USAGE.md)。

## 默认策略

首次运行默认为 `develop`：先建立基本经济和家防，再形成可用战斗编组。推荐通过叠加层切换模式：

| 模式 | 目的 | 核心行为 |
|---|---|---|
| `develop` | 安全扩张 | 建立工人、最低家防和信标侦察组，优先稳定资源循环 |
| `aggress` | 主动战斗 | 保留 `3 先锋 + 3 游侠` 家防，剩余单位集结搜索并攻击敌方 Core |
| `beacon` | 信标与远征 | 经济留在 Core 周边，战斗主力向信标和敌方 Core 集中推进 |
| `migrate` | Core 迁移 | 工人和战斗单位护送 Core 前往已验证的防守位置 |
| `lightning` | 闪电速攻 | 在 500~700 方环（挖空甜甜圈）内泊 Core，战斗单位各自独立路线猎杀刚复活、无护卫的敌方 Core（每杀 +5 资源）加速前期发育；建造顺序受资源容量锁死为首造先锋 |

生产、编队、撤退、斩首和 Core 决策的完整细节见[策略说明](docs/STRATEGY.md)。

## 仓库结构

| 路径 | 作用 |
|---|---|
| `arena_hero_dashboard_server.py` | API Key 初始化网关、会话鉴权和网页控制台 API |
| `arena_hero_agent_supervisor.py` | 在验证登录时启动、监控并停止真实 Agent 子进程 |
| `arena_hero_tactic.py` | SDK 连接、Tick 循环、热加载、遥测与错误处理入口 |
| `arena_hero_strategy.py` | 战术状态、寻路、经济、战斗、生产和 Core 决策 |
| `arena_hero_event_log.py` | 脱敏的中文结构化事件日志 |
| `arena_hero_route_overlay_server.py` | 仅本机监听的路线/统计/控制桥接服务 |
| `arena_hero_route_overlay/` | Chrome Manifest V3 叠加层扩展 |
| `start_*.ps1`, `stop_all.ps1` | Windows 启动与停止脚本 |
| `test_*.py` | 策略、服务、日志和端到端测试 |
| `deploy/` | systemd 与 Nginx 部署模板 |
| `docs/` | 使用、部署、配置、策略与发布说明 |

运行生成的 `.arena_hero_*.json`、遥测、日志、虚拟环境和凭据文件均已被 `.gitignore` 排除。

## 验证

```powershell
.\.venv\Scripts\python.exe -m compileall -q arena_hero_agent_supervisor.py arena_hero_dashboard_server.py arena_hero_tactic.py arena_hero_strategy.py arena_hero_event_log.py arena_hero_route_overlay_server.py
.\.venv\Scripts\python.exe -m unittest
node arena_hero_route_overlay\test_overlay_core.js
.\.venv\Scripts\python.exe -m pip check
```

GitHub Actions 会在 Windows 和 Linux 上运行相同的 Python/Node 核心检查。

## 安全

- 网页输入的 API Key 只传入 Agent 子进程环境；不会写入磁盘，后续请求使用随机会话 Token。
- 不要提交 `.env`、`.arena_hero_api_key.dpapi`、运行日志或状态快照。
- 公网部署必须通过 Nginx/Caddy 提供 HTTPS，应用仍只监听 `127.0.0.1:8766`。
- 叠加层服务只绑定回环地址，并只允许扩展来源提交控制和浏览器情报。
- 浏览器中的 Manual 指令优先于 Agent 对同一对象的指令。
- 发现凭据或安全问题时，请按 [SECURITY.md](SECURITY.md) 处理。

## 许可证

[MIT](LICENSE)
