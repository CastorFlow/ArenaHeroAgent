from __future__ import annotations

import os
import unittest
from pathlib import Path
from uuid import UUID

from arena_hero import (
    BeaconStatus,
    ChampionBeacon,
    CoreState,
    CoreView,
    Direction,
    MoveAction,
    ShootAction,
    UnitType,
    UnitView,
    WaitAction,
)

from arena_hero_strategy import SmartTactic, TacticMemory, _distance

# 复用 tactic 测试里的工厂函数，保证 fixture 与生产路径一致。
from test_arena_hero_tactic import (
    core,
    enemy_ranger,
    make_turn,
    ranger,
    vanguard,
)

CORE_ID = UUID("00000000-0000-4000-8000-000000000100")
RK_OUTER = UUID("00000000-0000-4000-8000-000000000a01")
RK_INNER = UUID("00000000-0000-4000-8000-000000000a02")
RK_MID = UUID("00000000-0000-4000-8000-000000000a03")
VG_OUTER = UUID("00000000-0000-4000-8000-000000000b01")
VG_INNER = UUID("00000000-0000-4000-8000-000000000b02")


class CometTests(unittest.TestCase):
    """哈雷彗星模块：外层优先抽调 / 半血替补 / 保留线 / 信标夺取收兵 / 无替补收兵。"""

    def setUp(self) -> None:
        # 控制文件写到临时路径，避免污染真实运行态。
        self._prev_control = os.environ.get("ARENA_HERO_CONTROL_FILE")
        os.environ["ARENA_HERO_CONTROL_FILE"] = str(
            Path("/tmp") / f".arena_hero_comet_test_{os.getpid()}.json"
        )

    def tearDown(self) -> None:
        if self._prev_control is None:
            os.environ.pop("ARENA_HERO_CONTROL_FILE", None)
        else:
            os.environ["ARENA_HERO_CONTROL_FILE"] = self._prev_control

    def _tactic(self) -> SmartTactic:
        tactic = SmartTactic(TacticMemory())
        tactic.memory.lightning_ring = (400, 600)
        tactic.memory.comet_active = True
        tactic.memory.comet_mode = "coordinate"
        tactic.memory.comet_target = (200, 200)
        tactic.memory.comet_vanguards = 1
        tactic.memory.comet_rangers = 2
        tactic.memory.comet_min_reserve_vanguards = 1
        tactic.memory.comet_min_reserve_rangers = 1
        return tactic

    def _seed_orbit_lanes(self, tactic: SmartTactic) -> None:
        """手填 lightning_orbit_lanes 模拟轨道分层：RK_OUTER 半径最大。"""
        tactic.memory.lightning_orbit_lanes[UnitType.RANGER.value] = {
            str(RK_OUTER): (600, 0),
            str(RK_MID): (500, 0),
            str(RK_INNER): (400, 0),
        }
        tactic.memory.lightning_orbit_lanes[UnitType.VANGUARD.value] = {
            str(VG_OUTER): (100, 0),
            str(VG_INNER): (100, 1),
        }

    def test_dispatch_prefers_outer_orbit(self) -> None:
        """抽调游侠时优先选 radius 最大的（外层）。"""
        tactic = self._tactic()
        self._seed_orbit_lanes(tactic)
        turn, _ = make_turn(
            own_core=core((0, 0)),
            units=(
                ranger((10, 10), RK_OUTER),
                ranger((10, -10), RK_INNER),
                ranger((-10, 10), RK_MID),
                vanguard((5, 5), VG_OUTER),
                vanguard((-5, -5), VG_INNER),
            ),
        )
        tactic._comet_reinforce(turn)
        members = tactic.memory.comet_member_ids
        self.assertIn(str(RK_OUTER), members, "外层游侠应被优先抽调")
        self.assertIn(str(RK_MID), members, "缺员应继续从次外层补")
        self.assertNotIn(str(RK_INNER), members, "满编后不再抽最内层")
        self.assertEqual(
            sum(1 for m in members if m in {str(RK_OUTER), str(RK_MID)}),
            2,
            "游侠应恰好满 2 员",
        )

    def test_wounded_member_replaced(self) -> None:
        """半血成员移入 retreating，从外层补同等数量满血替补。"""
        tactic = self._tactic()
        self._seed_orbit_lanes(tactic)
        # RK_OUTER 半血（已受伤），RK_MID 满血，RK_INNER 满血。
        turn, _ = make_turn(
            own_core=core((0, 0)),
            units=(
                ranger((10, 10), RK_OUTER, hp=1),
                ranger((10, -10), RK_INNER, hp=2),
                ranger((-10, 10), RK_MID, hp=2),
                vanguard((5, 5), VG_OUTER),
                vanguard((-5, -5), VG_INNER),
            ),
        )
        # 先把 RK_OUTER 当 member，再 reinforce → 应被移入 retreating 并补替补。
        tactic.memory.comet_member_ids.add(str(RK_OUTER))
        tactic._comet_reinforce(turn)
        self.assertIn(str(RK_OUTER), tactic.memory.comet_retreating_ids)
        self.assertNotIn(str(RK_OUTER), tactic.memory.comet_member_ids)
        # 补了 1 个满血替补（RK_MID 外层优先于 RK_INNER）。
        self.assertIn(str(RK_MID), tactic.memory.comet_member_ids)
        self.assertEqual(len(tactic.memory.comet_member_ids), 2)

    def test_reserve_line_blocks_dispatch(self) -> None:
        """保留线拦截：满血单位不足以保 Core 时不再抽调。"""
        tactic = self._tactic()
        tactic.memory.comet_rangers = 2
        tactic.memory.comet_min_reserve_rangers = 2  # 需留 2 满血
        self._seed_orbit_lanes(tactic)
        # 只有 3 个满血游侠：抽 2 去 comet 会剩 1 < 2 保留线 → 只能抽 1。
        turn, _ = make_turn(
            own_core=core((0, 0)),
            units=(
                ranger((10, 10), RK_OUTER, hp=2),
                ranger((10, -10), RK_INNER, hp=2),
                ranger((-10, 10), RK_MID, hp=2),
                vanguard((5, 5), VG_OUTER),
                vanguard((-5, -5), VG_INNER),
            ),
        )
        tactic._comet_reinforce(turn)
        # 3 满血 - 保留 2 = 最多抽 1。
        self.assertEqual(
            len(tactic.memory.comet_member_ids & {str(RK_OUTER), str(RK_INNER), str(RK_MID)}),
            1,
            "保留线应把抽调限制在 1 员",
        )

    def test_beacon_captured_cancels(self) -> None:
        """beacon 模式下信标被己方拾取 → 任务收兵。"""
        tactic = self._tactic()
        tactic.memory.comet_mode = "beacon"
        tactic.memory.comet_member_ids.add(str(RK_OUTER))
        own_core = core((0, 0))
        # carrier_id 设为己方游侠 → _owns_beacon 为真。
        beacon = ChampionBeacon(
            position=(50, 50),
            status=BeaconStatus.CARRIED,
            carrier_id=RK_OUTER,
        )
        turn, _ = make_turn(
            own_core=own_core,
            units=(ranger((50, 50), RK_OUTER), vanguard((5, 5), VG_OUTER)),
            beacon=beacon,
        )
        target = tactic._comet_resolve_target(turn)
        self.assertIsNone(target, "信标被己方夺取应返回 None 表示收兵")
        self.assertFalse(tactic.memory.comet_active, "任务应已关闭")
        self.assertEqual(tactic.memory.comet_member_ids, set())

    def test_no_replacement_cancels(self) -> None:
        """某兵种替补断档但另一兵种仍在前线 → 任务继续（不再过早取消）。

        旧行为：游侠凑不齐 2 员就 cancel 整个任务——会把仍在战斗的先锋也连带
        撤回。新行为：只要前线还有任一兵种的 member 在战斗 / 任一兵种还能补员，
        任务就继续。此处游侠 1 满血（被保留线吃光）、先锋 2 满血可抽 1 →
        先锋进 member，任务不取消。
        """
        tactic = self._tactic()
        tactic.memory.comet_rangers = 2
        tactic.memory.comet_min_reserve_rangers = 1
        # 只有 1 个满血游侠，另 1 个半血（不满足替补），无法凑齐 2 员。
        turn, _ = make_turn(
            own_core=core((0, 0)),
            units=(
                ranger((10, 10), RK_OUTER, hp=2),
                ranger((10, -10), RK_INNER, hp=1),  # 半血不能进 pool
                vanguard((5, 5), VG_OUTER),
                vanguard((-5, -5), VG_INNER),
            ),
        )
        # 不预置 member，直接 reinforce：游侠凑不齐，但先锋还能补 1 员进 member。
        tactic._comet_reinforce(turn)
        self.assertTrue(
            tactic.memory.comet_active,
            "游侠替补断档但先锋仍可派员时，任务应继续，不得过早取消",
        )
        # 先锋应被抽调进 member（前线仍有成员在战斗）。
        self.assertTrue(
            tactic.memory.comet_member_ids
            & {str(VG_OUTER), str(VG_INNER)},
            "先锋应被补员进 member 维持前线",
        )

    def test_squad_wiped_with_no_reserve_cancels(self) -> None:
        """失败取消：前线 0 member + 0 retreating + 基地凑不出任何满血替补 → 取消。

        这是用户定义的失败结束条件：全军被歼灭或全部负伤退下、且 Core 没有足够
        替补成员去替补 → 任务自动取消。构造：两兵种满血单位都低于保留线，无法
        抽出任何替补，且 member/retreating 均空。
        """
        tactic = self._tactic()
        tactic.memory.comet_vanguards = 1
        tactic.memory.comet_rangers = 1
        tactic.memory.comet_min_reserve_vanguards = 1
        tactic.memory.comet_min_reserve_rangers = 1
        # 每兵种仅 1 个满血单位，保留线各 1 → dispatchable 全为 0，抽不出任何替补。
        turn, _ = make_turn(
            own_core=core((0, 0)),
            units=(
                ranger((10, 10), RK_OUTER, hp=2),
                vanguard((5, 5), VG_OUTER),
            ),
        )
        tactic._comet_reinforce(turn)
        self.assertFalse(
            tactic.memory.comet_active,
            "前线全空 + 基地凑不出任何满血替补时应自动取消任务",
        )


class CometThreatAvoidanceTests(unittest.TestCase):
    """彗星小队遇敌不得送：射程内开火(复用格斗算法)、近旁有威胁不前压进火力圈。"""

    def setUp(self) -> None:
        self._prev_control = os.environ.get("ARENA_HERO_CONTROL_FILE")
        os.environ["ARENA_HERO_CONTROL_FILE"] = str(
            Path("/tmp") / f".arena_hero_comet_threat_test_{os.getpid()}.json"
        )

    def tearDown(self) -> None:
        if self._prev_control is None:
            os.environ.pop("ARENA_HERO_CONTROL_FILE", None)
        else:
            os.environ["ARENA_HERO_CONTROL_FILE"] = self._prev_control

    def _tactic_with_ranger_member(self) -> SmartTactic:
        tactic = SmartTactic(TacticMemory())
        tactic.memory.lightning_ring = (400, 600)
        tactic.memory.comet_active = True
        tactic.memory.comet_mode = "coordinate"
        tactic.memory.comet_target = (100, 0)
        tactic.memory.comet_rangers = 1
        tactic.memory.comet_vanguards = 0
        tactic.memory.comet_min_reserve_rangers = 0
        tactic.memory.comet_min_reserve_vanguards = 0
        # 直接把游侠设为彗星成员，跳过抽调逻辑，聚焦威胁响应。
        tactic.memory.comet_member_ids.add(str(RK_OUTER))
        return tactic

    def test_ranger_in_range_shoots_enemy_instead_of_advancing(self) -> None:
        """游侠距敌游侠 3 格(射程内)且在彗星目标方向 → 应开火而非移动送。

        复用打分制预瞄：射程内有合法射击线即开火，命中后本 tick 不再朝目标移动。
        """
        tactic = self._tactic_with_ranger_member()
        own_core = core((0, 0))
        # RK_OUTER 在 (50,0)，敌游侠在 (53,0)：直线射程 3，且敌在 comet_target(100,0)
        # 方向上——旧行为会继续往 (100,0) 冲进/停在敌射程内送；新行为应开火。
        turn, _ = make_turn(
            own_core=own_core,
            units=(ranger((50, 0), RK_OUTER),),
            enemies=(enemy_ranger((53, 0)),),
        )
        tactic.choose_actions(turn)
        action = turn.plan.unit_actions.get(RK_OUTER)
        self.assertIsInstance(
            action, ShootAction,
            "射程内的彗星游侠应开火(复用打分预瞄)，而非移动送进敌火力线",
        )

    def test_ranger_holds_when_threat_near_but_out_of_range(self) -> None:
        """敌游侠在警戒圈内(≤3)但无合法射击线(被障碍挡) → 原地待命不前压。

        构造：RK_OUTER 在 (50,0)，敌游侠在 (53,0)，中间 (51,0) 放障碍阻断射线。
        距离=3 ≤ COMET_THREAT_RADIUS → 触发威胁响应；射线被障碍挡 → 无合法射击
        → 原地 WAIT，绝不朝 (100,0) 继续推进进火力圈。
        """
        tactic = self._tactic_with_ranger_member()
        own_core = core((0, 0))
        turn, _ = make_turn(
            own_core=own_core,
            units=(ranger((50, 0), RK_OUTER),),
            enemies=(enemy_ranger((53, 0)),),
            obstacle_cells=((51, 0),),
        )
        tactic.choose_actions(turn)
        action = turn.plan.unit_actions.get(RK_OUTER)
        self.assertIsInstance(
            action, WaitAction,
            "近旁有威胁但射不到时应原地待命，不前压进敌火力射程送",
        )

    def test_ranger_does_not_step_into_enemy_range(self) -> None:
        """游侠距敌 4 格(射程外)且朝目标推进路径会进入敌 3 格射程 → 不走那格。

        RK_OUTER 在 (50,0)，敌游侠在 (54,0)：当前距离 4(安全)。comet_target=(100,0)
        朝东推进，下一格 (51,0) 距敌 3 → 进火力圈 → 应被安全推进剔除。新行为
        下游侠不得移动到 (51,0)；可选的安全格为原地 WAIT 或绕开。
        """
        tactic = self._tactic_with_ranger_member()
        own_core = core((0, 0))
        turn, _ = make_turn(
            own_core=own_core,
            units=(ranger((50, 0), RK_OUTER),),
            enemies=(enemy_ranger((54, 0)),),
        )
        tactic.choose_actions(turn)
        action = turn.plan.unit_actions.get(RK_OUTER)
        # 不允许是朝东的 MoveAction（那会进 (51,0) 距敌 3）。
        self.assertFalse(
            isinstance(action, MoveAction) and action.direction.value == (1, 0),
            "游侠不得推进进敌方火力射程(下一格距敌 ≤3)",
        )


class CometRallyTests(unittest.TestCase):
    """集合（Rally）：仅首批出发触发集合、近旁占位警戒、等齐队友再推进、替补不受影响。"""

    def setUp(self) -> None:
        self._prev_control = os.environ.get("ARENA_HERO_CONTROL_FILE")
        os.environ["ARENA_HERO_CONTROL_FILE"] = str(
            Path("/tmp") / f".arena_hero_comet_rally_test_{os.getpid()}.json"
        )

    def tearDown(self) -> None:
        if self._prev_control is None:
            os.environ.pop("ARENA_HERO_CONTROL_FILE", None)
        else:
            os.environ["ARENA_HERO_CONTROL_FILE"] = self._prev_control

    def _tactic_with_rally(self, *, distance: int = 10) -> SmartTactic:
        tactic = SmartTactic(TacticMemory())
        tactic.memory.lightning_ring = (400, 600)
        tactic.memory.comet_active = True
        tactic.memory.comet_mode = "coordinate"
        tactic.memory.comet_target = (100, 0)
        tactic.memory.comet_vanguards = 0
        tactic.memory.comet_rangers = 2
        tactic.memory.comet_min_reserve_vanguards = 0
        tactic.memory.comet_min_reserve_rangers = 0
        tactic.memory.comet_rally_enabled = True
        tactic.memory.comet_rally_distance = distance
        # 直接把两游侠设为 member，跳过抽调逻辑，聚焦集合行为。
        tactic.memory.comet_member_ids = {str(RK_OUTER), str(RK_INNER)}
        return tactic

    def test_rally_point_along_core_to_target(self) -> None:
        """集合点 = 沿 Core→目标方向距目标 comet_rally_distance 的格子。"""
        tactic = self._tactic_with_rally(distance=10)
        own_core = core((0, 0))
        turn, _ = make_turn(
            own_core=own_core,
            units=(
                ranger((50, 0), RK_OUTER),
                ranger((50, 10), RK_INNER),
            ),
        )
        rally = tactic._comet_rally_point(turn, (100, 0))
        # 目标(100,0)，Core(0,0)，方向正东，距离 10 → 集合点 (90,0)。
        self.assertEqual(rally, (90, 0))

    def test_member_not_at_rally_advances_toward_rally_not_target(self) -> None:
        """未到集合点近旁的成员向集合点推进，而非直奔目标。

        构造：RK_OUTER 在(50,0)，RK_INNER 在(50,50)，集合点(90,0)。
        RK_OUTER 离集合点近、RK_INNER 远。两者都还没到集合点近旁 → 都应朝集合点
        推进，不应朝目标(100,0)推进过头。
        """
        tactic = self._tactic_with_rally(distance=10)
        own_core = core((0, 0))
        turn, _ = make_turn(
            own_core=own_core,
            units=(
                ranger((50, 0), RK_OUTER),
                ranger((50, 50), RK_INNER),
            ),
        )
        tactic.choose_actions(turn)
        # RK_OUTER 在(50,0)，朝(90,0)推进应是东向(RIGHT)；不得是停在西/南/北。
        action_outer = turn.plan.unit_actions.get(RK_OUTER)
        self.assertTrue(
            isinstance(action_outer, MoveAction) and action_outer.direction is Direction.RIGHT,
            "未到集合点的成员应朝集合点推进(东向)，而非原地或反方向",
        )

    def test_member_at_rally_holds_waiting_for_squad(self) -> None:
        """已到集合点近旁的成员原地占位等待，不继续朝目标推进。

        构造：RK_OUTER 在(89,0)——距集合点(90,0)曼哈顿 1 ≤ COMET_RALLY_ARRIVE_RADIUS
        → 视为到达；RK_INNER 在(50,50)——还没到。RK_OUTER 应原地占位等待
        队友，不得继续朝(100,0)东向推进。
        """
        tactic = self._tactic_with_rally(distance=10)
        own_core = core((0, 0))
        turn, _ = make_turn(
            own_core=own_core,
            units=(
                ranger((89, 0), RK_OUTER),
                ranger((50, 50), RK_INNER),
            ),
        )
        tactic.choose_actions(turn)
        self.assertIn(
            str(RK_OUTER), tactic.memory.comet_rally_ready_ids,
            "到达集合点近旁的成员应被标记 rally-ready",
        )
        action_outer = turn.plan.unit_actions.get(RK_OUTER)
        self.assertFalse(
            isinstance(action_outer, MoveAction) and action_outer.direction is Direction.RIGHT,
            "已到集合点近旁的成员不得继续朝目标推进，应原地占位等待队友",
        )
        # 仍在集合阶段（rally_done=False）。
        self.assertFalse(
            tactic.memory.comet_rally_done,
            "队友未到齐前应保持集合阶段(rally_done=False)",
        )

    def test_all_ready_then_advance_to_target(self) -> None:
        """全员到齐 → 结束集合阶段，一起向目标推进。

        构造：RK_OUTER 在(89,0)、RK_INNER 在(90,1)，两者距集合点(90,0)曼哈顿
        均为 1 ≤ COMET_RALLY_ARRIVE_RADIUS → 均到达。处理顺序里后到的成员触发
        "全员到齐"，结束集合阶段（rally_done=True、rally_ready_ids 清空）。
        """
        tactic = self._tactic_with_rally(distance=10)
        own_core = core((0, 0))
        turn, _ = make_turn(
            own_core=own_core,
            units=(
                ranger((89, 0), RK_OUTER),
                ranger((90, 1), RK_INNER),  # 距集合点(90,0)曼哈顿 1 → 到达
            ),
        )
        tactic.choose_actions(turn)
        # 全员到齐后集合阶段结束，rally_ready_ids 应被清空、rally_done=True。
        self.assertFalse(
            tactic.memory.comet_rally_ready_ids,
            "全员到齐应结束集合阶段并清空 rally_ready_ids",
        )
        self.assertTrue(
            tactic.memory.comet_rally_done,
            "全员到齐应置 rally_done=True，后续替补直接奔目标",
        )

    def test_rally_at_obstacle_occupies_adjacent(self) -> None:
        """集合点是障碍物时，成员在其近邻可占格即算到达。

        构造：集合点(90,0)是障碍物。RK_OUTER 在(89,0)，距集合点曼哈顿 1 → 到达。
        _comet_at_rally 应返回 True（成员站集合点近邻可占格，集合点本身是障碍物
        也不影响）。
        """
        from arena_hero_strategy import MovementPlanner
        tactic = self._tactic_with_rally(distance=10)
        own_core = core((0, 0))
        turn, _ = make_turn(
            own_core=own_core,
            units=(
                ranger((89, 0), RK_OUTER),
                ranger((50, 50), RK_INNER),
            ),
            obstacle_cells=((90, 0),),
        )
        planner = MovementPlanner(turn, tactic.memory, [])
        rk_outer_unit = next(u for u in turn.rangers if u.id == RK_OUTER)
        self.assertTrue(
            tactic._comet_at_rally(turn, planner, rk_outer_unit, (90, 0)),
            "成员站在障碍物集合点的近邻格应被判为到达集合点",
        )


if __name__ == "__main__":
    unittest.main()
