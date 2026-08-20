# 紧急Bug修复总结

## 🐛 问题描述

**发现时间**: 2026-08-10 18:30
**严重等级**: 🔴 Critical
**症状**: 游侠只巡逻不攻击，发现敌方Core（零守卫）直接绕过

---

## 🔍 根本原因

### 代码缺陷位置

**文件**: `arena_hero_strategy.py`
**行**: 10428-10507（中轨游侠决策函数 `_choose_rangers_lightning`）

### 问题分析

中轨游侠在 **FAR威胁** 和 **无威胁** 两种情况下，**完全没有射击逻辑**：

```python
# arena_hero_strategy.py:10428-10470
def _choose_rangers_lightning(...):
    for ranger in mid_orbit_rangers:
        # FAR威胁 → 只移动到游击位置，不射击 ❌
        if threat is ThreatLevel.FAR:
            if planner.toward(ranger, kite_pos, "mid_orbit_snipe_FAR"):
                decisions.append(...)
                self.memory.decision_totals["mid_orbit:snipe_FAR"] += 1
                continue

        # 无威胁 → 只巡逻，不射击 ❌
        if planner.toward(ranger, scout, "mid_orbit_patrol"):
            decisions.append(...)
            self.memory.decision_totals["mid_orbit:patrol"] += 1
```

**缺失的功能**：
- ❌ 没有调用 `_ranger_shot_candidates()` 检测射程内目标
- ❌ 没有调用 `ranger.shoot()` 射击
- ❌ 移动和射击逻辑完全分离，导致即使敌人在射程内也不攻击

**对比**：开路游侠（breakthrough）有射击逻辑，但因为 `breakthrough_safe = False` 从未运行过。

---

## ✅ 修复方案

### 修改内容

在 **FAR威胁移动** 和 **无威胁巡逻** 之前，添加射击检测：

```python
# 修复后的代码（arena_hero_strategy.py:10428-10507）

for ranger in mid_orbit_rangers:
    # === 新增：射击优先 ===
    shot_candidates = list(
        self._ranger_shot_candidates(turn, ranger, planner)
    )
    if shot_candidates:
        # 优先射击已经overkill的目标（集火）
        target, cell = min(
            shot_candidates,
            key=lambda pair: (
                1 if assigned_damage[pair[0].id] >= _effective_hp(pair[0]) else 0,
                _enemy_role_priority(pair[0]),  # 先锋>游侠>工人
                _effective_hp(pair[0]),
                _distance(ranger.position, pair[0].position),
                pair[0].id.bytes,
            ),
        )
        ranger.shoot(target, expected_cell=cell)
        self._mark_ranger_shot(target, cell)
        assigned_damage[target.id] += 1
        decisions.append(
            f"ranger:{_short_id(ranger.id)} shoot target={_short_id(target.id)} "
            f"expected={cell} hp={target.health} role={target.type.value}"
        )
        self.memory.decision_totals["ranger:shoot"] += 1
        continue  # 射击后不再移动

    # FAR威胁 → 移动到游击位置（已有代码）
    if threat is ThreatLevel.FAR:
        ...

    # 无威胁 → 巡逻（已有代码）
    if planner.toward(ranger, scout, "mid_orbit_patrol"):
        ...
```

**核心改进**：
1. ✅ **射击优先于移动** - 如果射程内有敌人，先射击再说
2. ✅ **集火逻辑** - 优先攻击已经被其他游侠瞄准的目标（`assigned_damage`）
3. ✅ **角色优先级** - 先打先锋（威胁最大），再打游侠，最后打工人
4. ✅ **overkill判断** - 已经足够伤害击杀的目标排前面，避免浪费火力

---

## 🚀 部署状态

### 部署时间线

| 时刻 | 事件 |
|------|------|
| 18:30 | 发现bug（游侠不射击） |
| 18:35 | 完成代码分析，定位根本原因 |
| 18:36 | 编写修复代码 |
| 18:37 | 部署到 vps168（rsync） |
| 18:37 | 触发热加载（`touch arena_hero_strategy.py`） |
| 18:37:25 | ❌ 服务被TERM信号杀死（用户或系统操作） |
| 18:42 | ✅ 重新启动服务（systemctl start） |
| 18:42-现在 | 运行中，等待遇敌验证 |

### 当前状态

```bash
✅ 服务运行中
   - tick: 84411
   - population: 24 (2先锋 + 10游侠 + 12工人)
   - enemies: 0（暂无敌人，未触发射击）

✅ 修复代码已加载
   - 文件修改时间: 2026-08-10 18:37
   - 进程启动时间: 2026-08-10 18:42

⏳ 等待验证
   - 需要遇到敌方Core或敌方单位
   - 届时检查日志是否出现 "ranger:shoot"
```

---

## 📊 历史问题追溯

### 之前损失的游侠

**tick 83779 发生了什么**：

```json
{
  "tick": 83779,
  "title": "单位阵亡",
  "message": "游侠#2 在 [465, 42] 受到 1 点伤害并阵亡（遭受攻击）"
}
```

**真相**：
- 敌方先锋出现在 [465, 43]，生命4
- 你的游侠#2 在 [465, 42]，生命2
- ❌ **策略代码崩溃** - `AttributeError: 'TacticMemory' object has no attribute 'aggress_heal_rotations'`
- 💀 因为崩溃，**没有提交任何行动计划**，游侠站桩被打死

**已同时修复**：
```python
# arena_hero_strategy.py:412
@dataclass
class TacticMemory:
    ...
    aggress_heal_rotations: dict[str, int] = field(default_factory=dict)  # 新增
```

---

## 🧪 验证计划

### 等待自然遇敌

**预期行为**：
1. 游侠巡逻时发现敌方Core或单位
2. 进入射程（≤5格）
3. 日志出现：`ranger:XXXXXX shoot target=YYYYYY expected=(x,y) hp=N role=core/vanguard/ranger`
4. 统计中出现：`"ranger:shoot": N`

### 手动验证（可选）

如果想立即验证，可以通过 direct_session.py 手动介入：

```bash
# 停止自动agent
ssh -p 9393 root@vps168 "systemctl stop arena-hero-agent"

# 手动接管一个tick
ssh -p 9393 root@vps168 "cd /root/arenahero && .venv/bin/python direct_session.py"

# 查看state，如果有敌人在射程内，应该会看到shoot命令
```

---

## 📋 相关文档

- [日志系统诊断](LOGGING_DIAGNOSIS.md) - 完整的日志系统评估
- [日志修复总结](LOGGING_FIXES_SUMMARY.md) - 日志可读性改进
- [电子排布探测分析](ELECTRON_SHELL_EXPLORATION_ANALYSIS.md) - 轨道探测效率评估

---

## 🎯 下一步

### 立即

1. ✅ 修复已部署，运行中
2. ⏳ 等待遇敌验证射击功能

### 短期（今晚-明天）

3. 📊 收集战斗数据：
   - 射击命中率
   - 集火效果（多个游侠是否攻击同一目标）
   - 敌方Core击杀效率

4. 🐛 监控新bug：
   - 是否会overkill（浪费火力在已死目标）
   - 射击是否干扰巡逻路径

### 中期（本周）

5. 🔬 探测效率优化（见 ELECTRON_SHELL_EXPLORATION_ANALYSIS.md）：
   - 测试"混合排布"（前2个游侠内层，后续外圈）
   - 优化相位分布（减少视野重叠）

6. 📈 数据驱动调优：
   - 视野覆盖率统计
   - 敌方Core发现时间分布
   - 资源采集vs探索平衡

---

## 💡 教训总结

### 为什么这个bug存在这么久？

1. **代码分支未触发** - 开路游侠逻辑有射击，但 `breakthrough_safe = False` 导致从未运行
2. **中轨游侠是主力** - 所有游侠都在中轨巡逻，但这段代码没有射击
3. **日志不完整** - 之前决策日志截断在前8个，看不到游侠决策
4. **手动操控掩盖问题** - 你手动击杀了几个敌方Core，没意识到agent从不自动射击

### 如何避免类似问题？

✅ **已改进**：
- 日志现在显示所有单位决策（不截断）
- 统计中明确区分 `ranger:shoot` 和 `mid_orbit:patrol`

🔮 **未来改进**：
- 添加单元测试：模拟"游侠+敌方Core在射程内"场景，断言必须射击
- 添加监控告警：连续N个tick有敌人但无射击 → 触发通知
- 代码审查：所有战斗单位决策函数必须显式处理"射击优先于移动"

---

## ✅ 修复验证检查清单

- [x] 代码已修复（arena_hero_strategy.py:10428-10507）
- [x] 代码已部署到 vps168
- [x] 服务已重启并运行
- [ ] 实战验证射击功能（等待遇敌）
- [ ] 确认统计中出现 "ranger:shoot"
- [ ] 确认敌方Core被自动击杀（不需手动）
- [ ] 监控1-2小时，确认无新bug

---

**修复人**: Claude (via WSL)
**部署位置**: vps168:/root/arenahero/
**验证状态**: ⏳ 待遇敌验证
