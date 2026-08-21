# Arena Hero Lightning Agent

一个面向 Arena Hero 长期运行的社区战术 Agent。项目使用官方 Python SDK，当前**只保留并支持闪电模式（Lightning）**；旧的多模式入口、控制字段和不可达策略分支已移除。

项目内包含：

- API Key 登录与 Agent 初始化 Dashboard；
- Lightning 工人采集、分层轨道、防御、游侠火控和 Core 控制；
- 动态产兵比例、兵种上限、补兵阈值和战备资源；
- 可选 Comet 小队任务；
- 运行状态、路线、统计和中文事件日志；
- 可选 Chrome/Edge 地图叠加层；
- Windows 本地启动脚本，以及 Linux/VPS 的 systemd、Nginx HTTPS 模板。

项目当前固定的兼容基线为：

- Arena Hero gameplay rules v0.14
- Arena Hero HTTP/WebSocket API v0.1
- `arena-hero` Python SDK `>=0.2.9,<0.3`
- Python 3.11+

> 本项目不是 Arena Hero 官方客户端。Agent 会真实控制 API Key 所属账号中的单位。

## 登录和 12 小时会话

Dashboard 的密码框输入的是 **Arena Hero API Key**。登录时服务端会启动真实 Agent，并等待官方 SDK 鉴权成功且收到第一个可操作 Turn；只有成功后才返回控制台和 Lightning 默认配置。

- API Key 不写入控制文件、状态文件或浏览器存储。
- 浏览器只在当前标签页的 `sessionStorage` 保存随机 Dashboard 会话 Token。
- 这个 Token 是**最后一次活动后 12 小时失效**，每次成功认证的 Dashboard 请求都会重新延长 12 小时。
- 页面持续打开并正常轮询时，会话通常会持续续期。
- 网页会话失效只会要求重新输入同一个 API Key，**不会停止 VPS 或本机后台正在运行的 Agent**。
- Dashboard 服务停止或重启时，它监管的 Agent 子进程才会被停止；Agent 自身崩溃或用户显式停止服务也会结束运行。

## Windows 快速开始

在 PowerShell 中运行：

```powershell
.\setup.ps1
.\start_all.ps1
```

启动后浏览器会打开：

```text
http://127.0.0.1:8766/
```

输入有效的 Arena Hero API Key，即可初始化 Agent 和默认 Lightning 配置。

`start_all.ps1` 还会启动仅供本机扩展使用的叠加层桥接服务：

```text
http://127.0.0.1:8765/
```

加载扩展：

1. 在 Chrome/Edge 打开扩展管理页并启用开发者模式。
2. 选择“加载已解压的扩展程序”。
3. 选择仓库中的 `arena_hero_route_overlay` 目录。
4. 登录 API Key 所属 Arena Hero 账号并打开 Arena 页面。

停止本仓库启动的 Dashboard、Agent 和叠加层：

```powershell
.\stop_all.ps1
```

高级命令行方式仍可使用 `set_key.ps1` 和 `start_arena_hero.ps1`，通过当前 Windows 用户的 DPAPI 在前台运行 Agent。

## Linux / WSL2

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
./scripts/start_dashboard.sh
```

默认访问地址仍是：

```text
http://127.0.0.1:8766/
```

## VPS、默认地址和自定义域名

Dashboard 默认只监听 VPS 本机回环地址：

```text
http://127.0.0.1:8766/
```

它不是一个可以直接从公网浏览器打开的默认公网 URL。没有域名时，推荐建立 SSH 隧道：

```bash
ssh -L 8766:127.0.0.1:8766 <user>@<vps>
```

然后在本机访问：

```text
http://127.0.0.1:8766/
```

绑定自己的域名时，保持应用监听 `127.0.0.1:8766`，使用 Nginx 将 HTTPS 域名反向代理到它。仓库模板位于：

```text
deploy/nginx-arena-hero-bootstrap.conf.example
deploy/nginx-arena-hero.conf.example
```

把模板中的 `arena.example.com` 全部替换成自己的域名，重点修改：

```nginx
server_name arena.example.com;
ssl_certificate /etc/letsencrypt/live/arena.example.com/fullchain.pem;
ssl_certificate_key /etc/letsencrypt/live/arena.example.com/privkey.pem;
proxy_pass http://127.0.0.1:8766;
```

同时在 DNS 服务商处添加指向 VPS 的 `A` 记录；有 IPv6 时可增加 `AAAA` 记录。完整步骤见[部署文档](docs/DEPLOYMENT.md)。

> 不要把 Dashboard 改成 `0.0.0.0:8766` 后通过公网明文 HTTP 输入 API Key。

## Lightning 默认配置

首次有效登录会创建 `.arena_hero_control.json`。该文件不包含 API Key，默认值参考 [`.arena_hero_control.example.json`](.arena_hero_control.example.json)。主要控制项包括：

- Core 轨道半径、驻扎、目标坐标和转移样式；
- Core 退避、追击和追随信标；
- 建造队列、产兵比例、兵种上限、补兵阈值和战备资源；
- Comet 小队目标、人数、最低留守、伤员撤退和首次集合。

`star`、`march`、`fortify` 是 Lightning 内部的 Core 转移样式；`beacon`、`coordinate` 是 Comet 的目标类型，均不是可切换的顶层策略模式。

详细字段见[使用文档](docs/USAGE.md)，战术行为见[策略说明](docs/STRATEGY.md)。

## 仓库结构

| 路径 | 作用 |
|---|---|
| `arena_hero_dashboard_server.py` | API Key 初始化网关、会话鉴权和 Dashboard API |
| `arena_hero_agent_supervisor.py` | 启动、监控和停止真实 Agent 子进程 |
| `arena_hero_tactic.py` | SDK 连接、Turn 循环、热加载、遥测与错误处理入口 |
| `arena_hero_strategy.py` | Lightning 状态、寻路、经济、战斗、生产和 Core 决策 |
| `arena_hero_event_log.py` | 脱敏的中文结构化事件日志 |
| `arena_hero_route_overlay_server.py` | 本地路线、统计、控制和浏览器情报桥接服务 |
| `dashboard/` | 登录页和网页控制台 |
| `arena_hero_route_overlay/` | Chrome/Edge Manifest V3 叠加层 |
| `deploy/` | systemd 与 Nginx 部署模板 |
| `docs/` | 使用、策略、部署、安全与发布说明 |
| `test_*.py` | 策略、服务、日志和端到端测试 |

运行生成的 `.arena_hero_*.json`、JSONL、日志、虚拟环境和凭据文件均已被 `.gitignore` 排除。

## 验证

Windows：

```powershell
.\check_release.ps1
```

Linux/WSL2：

```bash
python -m compileall -q .
python -m pytest -q
node --check arena_hero_route_overlay/overlay-main.js
node --check arena_hero_route_overlay/bridge.js
node arena_hero_route_overlay/test_overlay_core.js
git diff --check
```

## 文档

- [使用与控制参数](docs/USAGE.md)
- [Lightning 策略说明](docs/STRATEGY.md)
- [VPS、systemd、域名与 HTTPS](docs/DEPLOYMENT.md)
- [安全策略](SECURITY.md)
- [发布检查](docs/RELEASING.md)
- [贡献指南](CONTRIBUTING.md)

## License

见 [LICENSE](LICENSE)。
