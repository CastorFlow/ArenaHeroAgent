# 贡献指南

## 兼容性

战术改动必须兼容 Arena Hero gameplay rules v0.14 和官方 Python SDK `>=0.2.9,<0.3`。不要重复实现 SDK 已提供的传输、重试、状态模型或动态定价逻辑。

每个 Turn 都是对上一状态的权威完整替换。记忆中的资源和敌人必须具有明确的过期机制，并在当前视野确认信息失效时立即清除。

## 开发环境

```powershell
.\setup.ps1
```

提交 Pull Request 前请运行以下检查：

```powershell
.\.venv\Scripts\python.exe -m compileall -q arena_hero_tactic.py arena_hero_strategy.py arena_hero_event_log.py arena_hero_route_overlay_server.py
.\.venv\Scripts\python.exe -m unittest
node arena_hero_route_overlay\test_overlay_core.js
.\.venv\Scripts\python.exe -m pip check
```

## 改动要求

- 新增或修改经济、战斗、移动、产兵、控制或持久化行为时，添加有针对性的测试。
- 测试夹具和日志中不得包含 API Key 或本地运行文件。
- 保持前台和后台两种启动方式可用。
- 战术常量或决策优先级发生实质变化时，同步更新 `docs/STRATEGY.md`。
- CLI 参数、环境变量、控制字段或叠加层操作流程变化时，同步更新 `docs/USAGE.md`。
- 如实记录提交失败或错过的 Tick，不得在日志中隐藏提交错误。
