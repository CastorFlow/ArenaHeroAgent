# 安全策略

## 凭据保护

禁止提交或发布以下内容：

- `.env`
- `.arena_hero_api_key.dpapi`
- Shell 历史、截图、Issue 正文或 CI 日志中的 API Key
- 不准备公开的运行遥测或状态快照

在 Windows 上，`set_key.ps1` 使用当前用户的 DPAPI 保存 Arena Hero API Key。加密文件与当前计算机和用户绑定，并已被 Git 忽略。在其他平台上，应通过平台密钥存储或本地且已忽略的 `.env` 文件注入 `ARENA_HERO_API_KEY`。

Agent、事件记录器和叠加层服务会主动省略键名中包含 `api`、`authorization`、`credential`、`secret` 或 `token` 的值。

## 本地叠加层边界

叠加层服务仅绑定 `127.0.0.1`。写入接口接受扩展程序来源，并拒绝普通网页来源。未增加身份验证和威胁模型前，不要将其修改为监听公共网络接口。

## 报告安全漏洞

仓库支持时，请使用 GitHub 私密漏洞报告功能。不要创建包含凭据、漏洞利用载荷或 Arena Hero 私有状态的公开 Issue。发送报告前，应先撤销已经暴露的 API Key。

## 发布派生仓库前

运行 [发布清单](docs/RELEASING.md)中的检查并查看 `git status --ignored`。如果派生仓库曾经跟踪过本地凭据文件，还必须检查完整 Git 历史。
