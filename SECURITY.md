# 安全策略

## 凭据保护

推荐使用 Dashboard 登录页输入 Arena Hero API Key。初始化流程具有以下边界：

- API Key 只通过子进程环境传给 `arena_hero_tactic.py`，不会写入控制文件、状态文件或日志。
- Dashboard 父进程只保留进程内随机 HMAC 密钥生成的不可逆指纹，用于识别同一个 Key 的重复登录。
- 鉴权成功并收到首个 Turn 后，浏览器获得随机会话 Token；Token 仅保存在当前标签页的 `sessionStorage` 中，并按活动时间滑动续期 12 小时。
- 更换 API Key 前应重启 Dashboard，避免误停另一个账号正在运行的 Agent。

高级命令行方式仍可使用 `set_key.ps1`。它通过当前 Windows 用户的 DPAPI 保存 Key；生成的 `.arena_hero_api_key.dpapi` 与当前计算机和用户绑定，并已被 Git 忽略。在其他平台直接运行 Agent 时，可用进程环境变量或本地且已忽略的 `.env` 注入 `ARENA_HERO_API_KEY`。

禁止提交或发布以下内容：

- `.env`、`.arena_hero_api_key.dpapi`。
- `.arena_hero_agent_status.json`、`.arena_hero_control.json` 及其他本地运行状态。
- Agent、Dashboard、叠加层日志和 JSONL 遥测。
- Shell 历史、截图、Issue 正文或 CI 日志中的 API Key、Bearer Token。
- 不准备公开的账号遥测、地图、敌我状态或浏览器快照。

Agent、事件记录器和叠加层服务会主动省略键名中包含 `api`、`authorization`、`credential`、`secret` 或 `token` 的值，但这不能替代发布前检查。

## Dashboard 网络边界

Dashboard 默认只监听 `127.0.0.1:8766`。本机使用该地址；VPS 无域名时推荐通过 SSH 隧道访问。绑定公网域名时：

- 应用仍保持监听回环地址。
- 使用自己控制的 Nginx/Caddy 反向代理。
- 必须先配置有效 HTTPS 证书，再通过公网提交 API Key。
- 不要直接向公网暴露未加密的 `0.0.0.0:8766`。

`ARENA_HERO_DASHBOARD_TRUST_PROXY=true` 只应在 Dashboard 仅能接收可信回环反代请求时启用。否则客户端可以伪造 `X-Forwarded-For`，削弱按来源地址执行的登录限流。

Dashboard 的静态登录页和 `/api/health` 公开可访问；控制、统计、日志等数据 API 都需要会话 Token。响应包含 CSP、`nosniff`、禁止嵌入和无引用来源等基础安全头。

## 本地叠加层边界

叠加层服务默认只绑定 `127.0.0.1:8765`。写入接口接受扩展程序来源，并拒绝普通网页来源。未增加独立身份验证和威胁模型前，不要将其修改为监听公共网络接口。

## 报告安全漏洞

仓库支持时，请使用 GitHub 私密漏洞报告功能。不要创建包含凭据、漏洞利用载荷或 Arena Hero 私有状态的公开 Issue。发送报告前，应先撤销已经暴露的 API Key。

## 发布派生仓库前

运行[发布清单](docs/RELEASING.md)中的检查并查看 `git status --ignored`。如果派生仓库曾经跟踪过本地凭据文件，还必须检查完整 Git 历史。发现真实 Key 时，应先在 Arena Hero 侧撤销和重新签发，再清理 Git 历史及已有 Release/CI artifact。
