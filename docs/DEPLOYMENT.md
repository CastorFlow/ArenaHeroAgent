# 部署、默认地址与自定义域名

本项目的推荐入口是 `arena_hero_dashboard_server.py`。服务启动时**不会预先读取或保存 Arena Hero API Key，也不会立即启动 Agent**。用户在登录页输入 API Key 后，服务会启动真实的 `arena_hero_tactic.py` 子进程；只有官方 SDK 完成鉴权并收到首个完整 Turn，网页才会获得控制台会话并加载默认配置。

## 1. 默认地址

Dashboard 默认配置：

```text
listen: 127.0.0.1:8766
URL:    http://127.0.0.1:8766/
```

- **Windows / WSL2 本机**：由于本机 WSL2 已启用 mirrored networking，Windows 浏览器可直接访问 `http://127.0.0.1:8766/`。
- **VPS 且没有域名**：推荐仍只监听 VPS 回环地址，通过 SSH 隧道访问：

  ```bash
  ssh -L 8766:127.0.0.1:8766 vps168
  ```

  隧道保持运行时，在本机浏览器打开 `http://127.0.0.1:8766/`。
- `--host 0.0.0.0` 会让服务可通过 `http://VPS_IP:8766/` 访问，但 API Key 会经过该连接传输。**不要在公网直接使用未加密的 HTTP 或裸露 8766 端口。**

路线叠加层桥接服务仍默认为 `http://127.0.0.1:8765/`，仅供本机 Chrome/Edge 扩展使用，不应暴露到公网。

## 2. Linux / VPS 安装

以下路径只是示例，可替换为自己的安装目录：

```bash
git clone git@github.com:CastorFlow/ArenaHeroAgent.git /opt/ArenaHeroAgent
cd /opt/ArenaHeroAgent
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
./scripts/start_dashboard.sh
```

启动后不要在服务器 shell 中设置 `ARENA_HERO_API_KEY`。在浏览器登录页输入 Key 即可；Key 只进入 Agent 子进程环境，不写入项目文件。

直接运行时可使用这些环境变量：

```bash
export ARENA_HERO_DASHBOARD_HOST=127.0.0.1
export ARENA_HERO_DASHBOARD_PORT=8766
export ARENA_HERO_AGENT_START_TIMEOUT=25
./scripts/start_dashboard.sh
```

## 3. systemd 常驻

仓库提供 `deploy/arena-hero-dashboard.service.example`。先修改：

- `User` / `Group`：运行项目的低权限用户。
- `WorkingDirectory`、`ExecStart`、`ReadWritePaths`：项目实际绝对路径。

然后安装：

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

service 中不配置 API Key。Dashboard 收到有效 Key 后才启动 Agent，服务退出时会停止由它监管的 Agent 子进程。

## 4. 绑定自己的域名

假设域名是 `arena.example.com`。

### 4.1 DNS

在域名 DNS 控制台增加：

- `A` 记录：`arena.example.com` 指向 VPS IPv4。
- 有 IPv6 时可同时增加 `AAAA` 记录。

等待解析生效后确认：

```bash
getent ahosts arena.example.com
```

### 4.2 先启用证书申请用的 Nginx 配置

首次申请证书时，最终配置引用的证书文件还不存在，不能直接启用最终模板。先复制临时 HTTP-only 模板并替换其中的域名：

```bash
sudo cp deploy/nginx-arena-hero-bootstrap.conf.example \
  /etc/nginx/sites-available/arena-hero
sudo editor /etc/nginx/sites-available/arena-hero
sudo ln -s /etc/nginx/sites-available/arena-hero /etc/nginx/sites-enabled/arena-hero
sudo nginx -t
sudo systemctl reload nginx
```

临时模板对普通请求只返回 `503 HTTPS setup pending`，不会把登录页暴露在公网 HTTP 上。Certbot 的 Nginx 插件会临时处理 ACME 验证。

如果 `/etc/nginx/sites-enabled/default` 会抢占同一域名或默认站点，可先移除该符号链接，再执行 `nginx -t`。

### 4.3 申请证书

确认 DNS 已指向 VPS、80 端口已由 Nginx 接收后运行：

```bash
sudo certbot certonly --nginx -d arena.example.com
```

成功后应存在：

```text
/etc/letsencrypt/live/arena.example.com/fullchain.pem
/etc/letsencrypt/live/arena.example.com/privkey.pem
```

### 4.4 切换到最终 HTTPS 反向代理

复制最终模板，至少替换两处 `server_name` 和两条证书路径中的示例域名：

```bash
sudo cp deploy/nginx-arena-hero.conf.example \
  /etc/nginx/sites-available/arena-hero
sudo editor /etc/nginx/sites-available/arena-hero
sudo nginx -t
sudo systemctl reload nginx
```

最终配置的关键内容是：

```nginx
server_name arena.example.com;
ssl_certificate /etc/letsencrypt/live/arena.example.com/fullchain.pem;
ssl_certificate_key /etc/letsencrypt/live/arena.example.com/privkey.pem;
proxy_pass http://127.0.0.1:8766;
```

模板会把 80 端口强制跳转到 HTTPS，并传递真实客户端地址。systemd 服务中应保留：

```text
Environment=ARENA_HERO_DASHBOARD_TRUST_PROXY=true
```

只有当请求必定经过自己控制的回环反向代理时才启用这个变量，否则客户端可以伪造 `X-Forwarded-For` 绕过应用层登录限流。

完成后访问：

```text
https://arena.example.com/
```

API Key 会在初始化请求中传输，因此不要在证书尚未生效时通过公网 HTTP 输入 Key。

## 5. 登录与默认配置行为

1. 浏览器 POST `/api/login`，请求体只包含本次输入的 API Key。
2. Dashboard 通过环境变量把 Key 交给真实 Agent 子进程。
3. 官方 SDK 鉴权成功且收到首个 Turn 后，Agent 写入不含凭据的生命周期状态。
4. Dashboard 首次运行时创建 `.arena_hero_control.json`，写入完整默认配置，默认模式为 `develop`。
5. Dashboard 返回随机的 12 小时会话 Token，后续控制请求不再携带 API Key。

Key 不会写入控制文件、状态文件或日志。父进程只保留不可逆的 HMAC 指纹，用来识别同一个 Key 的重复登录。若要切换到另一个 API Key，请先重启 Dashboard 服务，避免误停正在运行的账号 Agent。

## 6. 安全检查

公网发布前至少确认：

```bash
ss -ltnp | grep -E ':8765|:8766'
curl -fsS http://127.0.0.1:8766/api/health
sudo nginx -t
```

预期：

- 8766 只监听 `127.0.0.1`。
- 8765 不对公网开放。
- 域名强制跳转 HTTPS。
- `.env`、`.arena_hero_api_key.dpapi`、`.arena_hero_agent_status.json`、日志和运行状态均未进入 Git。
