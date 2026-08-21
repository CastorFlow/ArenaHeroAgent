"""验证阈值驱动的补兵逻辑 + 比例停造/全零囤资源。"""
import sys
sys.path.insert(0, ".")
from arena_hero_strategy import (
    SmartTactic,
    UnitType,
)

T = SmartTactic()


class U:
    def __init__(self, t):
        self.unit_type = t


class TurnStub:
    def __init__(self, rangers=0, workers=0, vanguards=0):
        self.rangers = [U(UnitType.RANGER)] * rangers
        self.workers = [U(UnitType.WORKER)] * workers
        self.vanguards = [U(UnitType.VANGUARD)] * vanguards
        self.units = self.rangers + self.workers + self.vanguards


def cfg(ratio, threshold=None, priority=None):
    T.memory.spawn_ratio = ratio
    T.memory.replenish_threshold = threshold or {"ranger": 0, "vanguard": 0, "worker": 0}
    T.memory.replenish_priority = priority or ["ranger", "vanguard", "worker"]


def cap_off():
    T.memory.unit_caps = {"worker": 0, "vanguard": 0, "ranger": 0}


cap_off()

# 1. 全零比例 → 囤资源
cfg({"ranger": 0, "vanguard": 0, "worker": 0})
assert T._lightning_ratio_spawn(TurnStub(10, 10, 10), {}) is None, "全零应囤资源"
print("✓ 全零比例→囤资源")

# 2. 某兵种比例=0 → 停造该兵种（即使该兵种归一化最低）
cfg({"ranger": 3, "vanguard": 0, "worker": 1}, priority=["vanguard", "ranger", "worker"])
# vg=0 但 vanguard share=0，不应补先锋；游侠归一化 0/3=0 < 工人 10/1=10 → 补游侠
assert T._lightning_ratio_spawn(TurnStub(0, 10, 0), {}) == UnitType.RANGER, "vanguard=0 不应补先锋"
print("✓ 比例=0 兵种停造")

# 3. 低于阈值 → 按优先级补
cfg({"ranger": 3, "vanguard": 1, "worker": 1},
    {"ranger": 5, "vanguard": 3, "worker": 2},
    priority=["ranger", "vanguard", "worker"])
# 当前 rk=2 < 5, vg=0 < 3, wk=8 >= 2 → 优先补游侠
assert T._lightning_ratio_spawn(TurnStub(2, 8, 0), {}) == UnitType.RANGER, "优先级游侠优先"
print("✓ 低于阈值→按优先级补")

# 4. 多个低于阈值，优先级靠前的先补
cfg({"ranger": 3, "vanguard": 1, "worker": 1},
    {"ranger": 5, "vanguard": 3, "worker": 2},
    priority=["worker", "vanguard", "ranger"])
# 当前 rk=2 < 5, vg=0 < 3, wk=1 < 2 → 优先补工人
assert T._lightning_ratio_spawn(TurnStub(2, 1, 0), {}) == UnitType.WORKER, "优先级工人优先"
print("✓ 多兵种低于阈值→优先级决定")

# 5. 无低于阈值 → 按比例趋近
cfg({"ranger": 3, "vanguard": 1, "worker": 1},
    {"ranger": 0, "vanguard": 0, "worker": 0})
# rk=9/3=3, wk=1/1=1, vg=0/1=0 → 先锋归一化最低
assert T._lightning_ratio_spawn(TurnStub(9, 1, 0), {}) == UnitType.VANGUARD, "比例趋近补先锋"
print("✓ 无阈值触发→按比例趋近")

# 6. 封顶兜底：游侠封顶 2，当前 rk=2/wk=0/vg=0，比例 3:1:1 → 应改补工人或先锋
T.memory.unit_caps = {"worker": 0, "vanguard": 0, "ranger": 2}
cfg({"ranger": 3, "vanguard": 1, "worker": 1})
# 阈值 0，比例趋近：先锋归一化 0 < 工人 0 平局按优先级游侠→但游侠封顶→换工人
# 优先级默认 ranger>vanguard>worker；ranked 排序：ranger(0/3=0),vanguard(0/1=0),worker(0/1=0)
# 平局按 priority_types.index → ranger 先，ranger 封顶 → 找 vanguard 未封顶
result = T._lightning_ratio_spawn(TurnStub(2, 0, 0), {})
assert result in (UnitType.VANGUARD, UnitType.WORKER), f"封顶兜底异常: {result}"
print(f"✓ 封顶兜底→改补 {result.name}")

# 7. 全封顶 → None
T.memory.unit_caps = {"worker": 1, "vanguard": 1, "ranger": 2}
cfg({"ranger": 3, "vanguard": 1, "worker": 1})
assert T._lightning_ratio_spawn(TurnStub(2, 1, 1), {}) is None, "全封顶应停"
print("✓ 全封顶→None")

print("\n全部通过 ✅")
