# Linux / VPS 部署、默认地址与自定义域名

本文部署的是 Dashboard 和由其监管的 Lightning Agent。API Key 不写进 systemd unit、仓库或服务器配置文件，而是在网页登录时临时传给 Agent 子进程。

## 1. 网络边界与默认地址

Dashboard 默认监听：

```text
127.0.0.1:8766
```

VPS 本机地址是：

```text
http://127.0.0.1:8766/
```

这不是默认公网地址。除非配置 SSH 隧道或 HTTPS 反向代理，否则远程电脑不能直接打开它。

可选浏览器扩展桥接服务默认监听：

```text
127.0.0.1:8765
```

8765 只适合本机扩展，不要暴露公网。纯 VPS Dashboard 部署不需要启动 8765。

## 2. 安装项目

以下以 `/opt/ArenaHeroAgent` 为例，可替换成自己的安装目录：

```bash
git clone git@github.com:CastorFlow/ArenaHeroAgent.git /opt/ArenaHeroAgent
cd /opt/ArenaHeroAgent
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
./scripts/start_dashboard.sh
```

直接运行可使用：

```bash
export ARENA_HERO_DASHBOARD_HOST=127.0.0.1
export ARENA_HERO_DASHBOARD_PORT=8766
export ARENA_HERO_AGENT_START_TIMEOUT=25
./scripts/start_dashboard.sh
```

不要在服务器 shell、service 文件或 `.env` 中预置用于网页初始化的 API Key。浏览器提交 Key 后，它只进入 Agent 子进程环境。

## 3. 无域名访问

在本地电脑建立 SSH 隧道：

```bash
ssh -L 8766:127.0.0.1:8766 <user>@<vps>
```

保持 SSH 连接打开，然后本机浏览器访问：

```text
http://127.0.0.1:8766/
```

这时 API Key 通过 SSH 加密隧道传输，不需要把 8766 开到公网。

## 4. systemd 常驻

仓库提供：

```text
deploy/arena-hero-dashboard.service.example
```

先按实际环境修改：

- `User`、`Group`；
- `WorkingDirectory`；
- `ExecStart`；
- `ReadWritePaths`。

示例模板保持：

```text
Environment=ARENA_HERO_DASHBOARD_HOST=127.0.0.1
Environment=ARENA_HERO_DASHBOARD_PORT=8766
```

安装并启动：

```bash
sudo cp deploy/arena-hero-dashboard.service.example \
  /etc/systemd/system/arena-hero-dashboard.service
sudo systemctl daemon-reload
sudo systemctl enable --now arena-hero-dashboard
sudo systemctl status arena-hero-dashboard
```

查看日志：

```bash
journalctl -u arena-hero-dashboard -f
```

Dashboard 收到有效 Key 后才启动 Agent。只要 systemd 中的 Dashboard 服务和它监管的 Agent 子进程仍在运行，关闭网页或网页会话失效都不会让 Agent 停止。Dashboard 服务停止或重启时会停止当前监管的 Agent。

## 5. 绑定自己的域名

下面假设域名为 `arena.example.com`。

### 5.1 配置 DNS

在 DNS 服务商处添加：

```text
A     arena.example.com    -> VPS IPv4
AAAA  arena.example.com    -> VPS IPv6（可选）
```

确认解析：

```bash
getent ahosts arena.example.com
```

### 5.2 安装 Nginx 和 Certbot

Debian/Ubuntu 示例：

```bash
sudo apt-get update
sudo apt-get install -y nginx certbot python3-certbot-nginx
```

确保 VPS 的 80/443 端口可被公网访问。修改防火墙或云平台安全组属于部署环境操作，应按自己的安全策略执行。

### 5.3 启用临时证书申请配置

复制模板：

```bash
sudo cp deploy/nginx-arena-hero-bootstrap.conf.example \
  /etc/nginx/sites-available/arena-hero
sudo editor /etc/nginx/sites-available/arena-hero
```

把：

```nginx
server_name arena.example.com;
```

替换成自己的域名。然后启用：

```bash
sudo ln -s /etc/nginx/sites-available/arena-hero \
  /etc/nginx/sites-enabled/arena-hero
sudo nginx -t
sudo systemctl reload nginx
```

临时模板只返回 `HTTPS setup pending`，不会通过公网 HTTP 展示 API Key 登录页。

### 5.4 申请 HTTPS 证书

```bash
sudo certbot certonly --nginx -d arena.example.com
```

证书通常生成在：

```text
/etc/letsencrypt/live/arena.example.com/fullchain.pem
/etc/letsencrypt/live/arena.example.com/privkey.pem
```

### 5.5 切换最终反向代理配置

复制最终模板：

```bash
sudo cp deploy/nginx-arena-hero.conf.example \
  /etc/nginx/sites-available/arena-hero
sudo editor /etc/nginx/sites-available/arena-hero
```

需要修改的关键位置：

```nginx
server_name arena.example.com;
ssl_certificate /etc/letsencrypt/live/arena.example.com/fullchain.pem;
ssl_certificate_key /etc/letsencrypt/live/arena.example.com/privkey.pem;
proxy_pass http://127.0.0.1:8766;
```

模板中有两个 `server_name`，证书路径中也各包含一次示例域名，必须全部替换。检查并重载：

```bash
sudo nginx -t
sudo systemctl reload nginx
```

之后访问：

```text
https://arena.example.com/
```

应用本身仍监听 `127.0.0.1:8766`，Nginx 负责公网 HTTPS。不要把应用监听地址改成 `0.0.0.0` 来绕过反向代理。

### 5.6 可信代理设置

Nginx 模板会传递真实客户端地址。systemd 模板中的：

```text
Environment=ARENA_HERO_DASHBOARD_TRUST_PROXY=true
```

只应在 Dashboard 只能接收自己控制的回环反代请求时启用。如果直接把 Dashboard 暴露给不可信网络却仍信任转发头，客户端可能伪造 `X-Forwarded-For`，削弱登录限流。

## 6. 登录、默认配置与 12 小时会话

登录流程：

1. 浏览器向 `/api/login` 提交本次输入的 API Key。
2. Dashboard 仅通过子进程环境把 Key 交给真实 Agent。
3. 官方 SDK 鉴权成功并收到首个可操作 Turn 后，Agent 发布不含凭据的生命周期状态。
4. 首次成功登录会创建 `.arena_hero_control.json`，写入 Lightning 默认配置。
5. Dashboard 返回随机网页会话 Token，后续请求不再携带 API Key。

会话 Token 使用**滑动 12 小时空闲期限**：

- 每次成功认证请求都从当前时间重新延长 12 小时；
- 页面持续轮询时通常不会自动退出；
- 最后一次活动后连续 12 小时无请求才失效；
- 失效后输入同一个 API Key 可取得新 Token；
- Token 失效不会停止 Agent，也不会让 VPS 服务在 12 小时后自动停止。

父进程只保留进程内随机 HMAC 生成的 Key 指纹，用来判断是否为同一个 Key。不同 Key 尝试登录正在运行的 Dashboard 会得到冲突；切换账号前应先重启 Dashboard 服务。

## 7. 更新部署

建议先停止服务、拉取代码、更新依赖、运行测试，再启动：

```bash
sudo systemctl stop arena-hero-dashboard
cd /opt/ArenaHeroAgent
git pull --ff-only
. .venv/bin/activate
python -m pip install -r requirements.txt
python -m pytest -q
sudo systemctl start arena-hero-dashboard
sudo systemctl status arena-hero-dashboard
```

停止 Dashboard 会停止它监管的 Agent，因此应在可以接受游戏中断时更新。

## 8. 安全检查

```bash
ss -ltnp | grep -E ':8765|:8766'
curl -fsS http://127.0.0.1:8766/api/health
sudo nginx -t
```

预期：

- 8766 只监听 `127.0.0.1`；
- 8765 未在 VPS 上启动，或只监听回环；
- 域名的 HTTP 请求跳转 HTTPS；
- API Key、会话 Token、运行状态、JSONL 和日志未进入 Git；
- `.arena_hero_control.json` 不包含凭据。

完整安全边界见仓库根目录的 [SECURITY.md](../SECURITY.md)。
