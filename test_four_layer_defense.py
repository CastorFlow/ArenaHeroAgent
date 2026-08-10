"""
综合测试：四层轨道职责 + 分层防御系统

测试覆盖：
1. Phase 1: 外圈优先轨道密度分配
2. Phase 2: Core规避 + 工人肉盾行为
3. Phase 3: 游侠分层防御 + 开路战术
"""

import unittest
from unittest.mock import Mock, MagicMock
from arena_hero_strategy import ArenaHeroTactic, UnitType
from arena_hero import Turn, CoreView, UnitView, Worker, Ranger, Vanguard


class TestFourLayerDefense(unittest.TestCase):
    """四层轨道职责 + 分层防御综合测试"""

    def setUp(self):
        """初始化测试环境"""
        self.tactic = ArenaHeroTactic()
        self.turn = Mock(spec=Turn)
        self.turn.tick = 100
        self.turn.resource_space = 1000

        # 模拟Core位置
        self.core = Mock()
        self.core.position = (0, 0)
        self.core.id = "core-1"
        self.turn.core = self.core

        # 模拟可见敌人列表
        self.turn.visible_enemies = []

    def test_phase1_orbit_density_allocation(self):
        """Phase 1: 测试外圈优先轨道密度分配"""
        # 模拟12个游侠（开路4 + 中轨8）
        rangers = []
        for i in range(12):
            ranger = Mock(spec=Ranger)
            ranger.id = f"ranger-{i}"
            rangers.append(ranger)

        self.turn.rangers = rangers
        self.turn.vanguards = []
        self.turn.workers = []

        # 调用分配逻辑
        assignments = self.tactic._lightning_assign_orbit_lanes_dynamic(
            self.turn, UnitType.RANGER
        )

        # 验证分配结果
        self.assertEqual(len(assignments), 8)  # 8个中轨游侠（跳过开路4个）

        # 验证至少有多层轨道
        radii = {info["radius"] for info in assignments.values()}
        self.assertGreater(len(radii), 1, "应该有多个不同的轨道半径")

        # 验证外层轨道密度更高
        radius_counts = {}
        for info in assignments.values():
            r = info["radius"]
            radius_counts[r] = radius_counts.get(r, 0) + 1

        sorted_radii = sorted(radius_counts.keys())
        if len(sorted_radii) >= 2:
            outer_radius = sorted_radii[-1]
            inner_radius = sorted_radii[0]
            outer_count = radius_counts[outer_radius]
            inner_count = radius_counts[inner_radius]

            # 外圈周长更大，应该分配更多单位
            self.assertGreaterEqual(
                outer_count, inner_count,
                f"外圈(r={outer_radius})应该≥内圈(r={inner_radius})的单位数"
            )

    def test_phase2_core_avoidance(self):
        """Phase 2: 测试Core象限规避"""
        # 在Core右侧放置敌人
        enemy = Mock(spec=UnitView)
        enemy.position = (10, 0)
        enemy.unit_type = UnitType.RANGER
        self.turn.visible_enemies = [enemy]

        # Core当前在原点，巡逻方向朝右
        waypoint = self.tactic._lightning_patrol_waypoint(self.turn)

        # Core应该规避右侧（敌人所在象限）
        # 预期：Core不会往右上(+,+)或右下(+,-)走，会选择左侧象限
        if waypoint:
            self.assertLess(waypoint[0], 50, "Core应该规避右侧敌人，不往东走")

    def test_phase2_worker_meatshield_NEAR(self):
        """Phase 2: 测试工人肉盾行为（NEAR威胁）"""
        # 模拟NEAR威胁（距离Core ≤6）
        enemy = Mock(spec=UnitView)
        enemy.position = (5, 0)  # 距离Core=5
        enemy.unit_type = UnitType.VANGUARD
        self.turn.visible_enemies = [enemy]

        # 模拟3个空手工人，距离Core较远
        workers = []
        for i in range(3):
            worker = Mock(spec=Worker)
            worker.id = f"worker-{i}"
            worker.position = (20 + i, 20 + i)  # 远离Core
            worker.cargo = 0
            workers.append(worker)

        self.turn.workers = workers
        self.turn.rangers = []
        self.turn.vanguards = []

        # 调用工人逻辑（应该触发肉盾行为）
        planner = Mock()
        planner.toward = Mock(return_value=True)
        acted_units = set()
        decisions = []

        self.tactic._choose_workers(self.turn, planner, acted_units, decisions)

        # 验证：所有空手工人都尝试回防
        meatshield_decisions = [d for d in decisions if "meatshield" in d]
        self.assertGreater(len(meatshield_decisions), 0, "应该有工人执行肉盾行为")

    def test_phase3_ranger_defend_NEAR(self):
        """Phase 3: 测试游侠NEAR威胁回防"""
        # 模拟NEAR威胁
        enemy = Mock(spec=UnitView)
        enemy.position = (4, 0)  # 距离Core=4 < 6
        enemy.unit_type = UnitType.RANGER
        self.turn.visible_enemies = [enemy]

        # 模拟6个游侠
        rangers = []
        for i in range(6):
            ranger = Mock(spec=Ranger)
            ranger.id = f"ranger-{i}"
            ranger.position = (50 + i*10, 50 + i*10)  # 远离Core
            ranger.wait = Mock()
            rangers.append(ranger)

        self.turn.rangers = rangers
        self.turn.vanguards = []
        self.turn.workers = []

        planner = Mock()
        acted_units = set()
        decisions = []

        # Mock _lightning_step_toward
        self.tactic._lightning_step_toward = Mock(return_value=True)

        self.tactic._choose_rangers_lightning(
            self.turn, planner, acted_units, decisions
        )

        # 验证：所有游侠都执行NEAR回防
        near_decisions = [d for d in decisions if "defend_NEAR" in d]
        self.assertEqual(len(near_decisions), 6, "所有6个游侠应该执行NEAR回防")

    def test_phase3_ranger_defend_MID(self):
        """Phase 3: 测试游侠MID威胁集结围攻"""
        # 模拟MID威胁
        enemy = Mock(spec=UnitView)
        enemy.position = (15, 0)  # 距离Core=15，在MID范围（6<d≤20）
        enemy.unit_type = UnitType.VANGUARD
        self.turn.visible_enemies = [enemy]

        # 模拟8个游侠
        rangers = []
        for i in range(8):
            ranger = Mock(spec=Ranger)
            ranger.id = f"ranger-{i}"
            ranger.position = (30 + i*5, 30)
            ranger.wait = Mock()
            rangers.append(ranger)

        self.turn.rangers = rangers
        self.turn.vanguards = []
        self.turn.workers = []

        planner = Mock()
        acted_units = set()
        decisions = []

        # Mock helper函数
        self.tactic._lightning_step_toward = Mock(return_value=True)
        self.tactic._lightning_intercept_position = Mock(return_value=(12, 0))

        self.tactic._choose_rangers_lightning(
            self.turn, planner, acted_units, decisions
        )

        # 验证：所有游侠都执行MID集结
        mid_decisions = [d for d in decisions if "defend_MID" in d]
        self.assertEqual(len(mid_decisions), 8, "所有8个游侠应该执行MID集结围攻")

    def test_phase3_breakthrough_flee_on_ranger(self):
        """Phase 3: 测试开路游侠见敌方游侠时逃跑"""
        # 开路游侠遇到敌方游侠
        enemy = Mock(spec=UnitView)
        enemy.position = (100, 0)
        enemy.unit_type = UnitType.RANGER
        self.turn.visible_enemies = [enemy]

        # 模拟4个开路游侠
        rangers = []
        for i in range(4):
            ranger = Mock(spec=Ranger)
            ranger.id = f"ranger-{i}"
            ranger.position = (90 + i*5, 0)
            ranger.wait = Mock()
            rangers.append(ranger)

        self.turn.rangers = rangers
        self.turn.vanguards = []
        self.turn.workers = []

        planner = Mock()
        acted_units = set()
        decisions = []

        # Mock helper
        self.tactic._lightning_step_toward = Mock(return_value=True)
        self.tactic._lightning_breakthrough_target = Mock(return_value=(80, 0))

        self.tactic._choose_rangers_lightning(
            self.turn, planner, acted_units, decisions
        )

        # 验证：开路游侠执行逃跑
        flee_decisions = [d for d in decisions if "breakthrough_flee" in d]
        self.assertGreater(len(flee_decisions), 0, "开路游侠应该逃跑（见敌方游侠）")

    def test_phase3_breakthrough_kite_single_vanguard(self):
        """Phase 3: 测试开路游侠1v1先锋时游击"""
        # 开路游侠遇到单个敌方先锋
        enemy = Mock(spec=UnitView)
        enemy.position = (100, 0)
        enemy.unit_type = UnitType.VANGUARD
        self.turn.visible_enemies = [enemy]

        # 1个开路游侠
        ranger = Mock(spec=Ranger)
        ranger.id = "ranger-0"
        ranger.position = (95, 0)  # 距离先锋5格
        ranger.wait = Mock()

        self.turn.rangers = [ranger]
        self.turn.vanguards = []
        self.turn.workers = []

        planner = Mock()
        acted_units = set()
        decisions = []

        # Mock helper
        self.tactic._lightning_step_toward = Mock(return_value=True)
        self.tactic._lightning_kiting_position = Mock(return_value=(93, 0))
        self.tactic._lightning_breakthrough_target = Mock(return_value=(80, 0))

        self.tactic._choose_rangers_lightning(
            self.turn, planner, acted_units, decisions
        )

        # 验证：开路游侠执行游击
        kite_decisions = [d for d in decisions if "breakthrough_kite" in d]
        self.assertEqual(len(kite_decisions), 1, "开路游侠应该游击（1v1先锋）")

    def test_phase3_mid_orbit_snipe_FAR(self):
        """Phase 3: 测试中轨游侠FAR威胁狙击驱离"""
        # 模拟FAR威胁
        enemy = Mock(spec=UnitView)
        enemy.position = (35, 0)  # 距离Core=35，在FAR范围（20<d≤40）
        enemy.unit_type = UnitType.RANGER
        self.turn.visible_enemies = [enemy]

        # 模拟10个游侠（4开路+6中轨）
        rangers = []
        for i in range(10):
            ranger = Mock(spec=Ranger)
            ranger.id = f"ranger-{i}"
            if i < 4:
                ranger.position = (200 + i*20, 0)  # 开路游侠远离
            else:
                ranger.position = (30 + (i-4)*5, 0)  # 中轨游侠靠近威胁
            ranger.wait = Mock()
            rangers.append(ranger)

        self.turn.rangers = rangers
        self.turn.vanguards = []
        self.turn.workers = []

        planner = Mock()
        acted_units = set()
        decisions = []

        # Mock helper
        self.tactic._lightning_step_toward = Mock(return_value=True)
        self.tactic._lightning_kiting_position = Mock(return_value=(33, 0))
        self.tactic._lightning_breakthrough_target = Mock(return_value=(180, 0))
        self.tactic._lightning_orbit_waypoint = Mock(return_value=(25, 0))

        self.tactic._choose_rangers_lightning(
            self.turn, planner, acted_units, decisions
        )

        # 验证：靠近的中轨游侠执行FAR狙击
        snipe_decisions = [d for d in decisions if "snipe_FAR" in d]
        self.assertGreater(len(snipe_decisions), 0, "中轨游侠应该狙击FAR威胁")


if __name__ == "__main__":
    unittest.main()
