# 发布清单

本仓库发布的是长期运行战术源码、测试、Windows 辅助脚本和可选浏览器叠加层，不发布本地状态或账号数据。

## 1. 兼容基线

发布前确认：

- Python 最低版本仍为 3.11。
- `requirements.txt` 使用官方 `arena-hero>=0.2.9,<0.3`。
- 策略假设仍对应 gameplay rules v0.14。
- 如果服务器、SDK 或规则版本变化，README、使用文档和策略文档已同步更新。

## 2. 本地检查

```powershell
.\check_release.ps1
```

检查内容包括：

- Python 编译。
- 全套 `unittest`。
- 浏览器叠加层 Node 测试。
- `pip check`。
- Git 空白错误。
- 禁止跟踪的凭据、运行状态和日志文件名。
- 常见 DPAPI blob、Bearer Token 和明文 API Key 形态。

检查当前仓库状态：

```powershell
git status --short
git status --ignored --short
git diff --stat
```

运行中的 `.arena_hero_*.json`、`.env`、`.arena_hero_api_key.dpapi`、`*.jsonl` 和 `agent*.log` 应只出现在 ignored 列表中。

## 3. Git 历史检查

如果任何凭据文件曾被暂存或提交，仅添加 `.gitignore` 不够。发布前检查历史文件名：

```powershell
git log --all --name-only --pretty=format: |
    Sort-Object -Unique |
    Select-String -Pattern '\.env|api_key|credential|secret|token'
```

发现真实 Key 后应立即在 Arena Hero 侧吊销并重新签发，再使用适当的历史清理工具处理仓库。不要把旧 Key 留在 issue、PR、CI artifact 或 release archive 中。

## 4. 发布内容

应包含：

- Python 源码和 PowerShell 脚本。
- `arena_hero_route_overlay/` 扩展源码。
- 测试和 CI。
- README、用法、策略、安全、贡献和许可证文件。
- `.env.example` 与 `.arena_hero_control.example.json`。

不应包含：

- 虚拟环境、缓存、构建目录。
- API Key 或 DPAPI 文件。
- 战术记忆、路线、控制状态、统计、浏览器情报。
- 遥测、中文事件、后台输出。
- 个人截图、账号标识或未脱敏的敌我状态导出。

## 5. 建议的 Git 流程

```powershell
git switch -c release/vX.Y.Z
git add --all
git diff --cached --check
git status --short
git commit -m "release: vX.Y.Z"
git tag -a vX.Y.Z -m "Arena Hero adaptive tactic vX.Y.Z"
git push origin release/vX.Y.Z --follow-tags
```

创建 Pull Request 后等待 Windows/Linux CI 通过，再合并和生成 GitHub Release。Release notes 至少说明：

- 兼容的 Arena Hero 规则和 SDK 版本。
- 经济、产兵、编队、战斗或迁移策略变化。
- 控制字段或运行文件格式变化。
- 已知风险和升级步骤。

不要在没有明确版本选择时直接照抄示例中的 `vX.Y.Z`。
