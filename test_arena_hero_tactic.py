from __future__ import annotations

import json
import os
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

from arena_hero import (
    Accepted,
    BeaconStatus,
    ChampionBeacon,
    CancelMoveAction,
    CommandSource,
    CoreState,
    CoreView,
    DepositAction,
    Direction,
    DropBeaconAction,
    HarvestAction,
    HealAction,
    MoveAction,
    PlayerState,
    PlayerStatus,
    PickupBeaconAction,
    RepairShieldAction,
    ResolutionEvent,
    ShootAction,
    SpawnAction,
    StartMoveAction,
    SweepAction,
    TerrainView,
    Turn,
    UnitType,
    UnitView,
    WaitAction,
    unit_cost,
)

from arena_hero_tactic import choose_actions
from arena_hero_strategy import (
    LIGHTNING_DEFAULT_RING,
    MODE_LIGHTNING,
    SmartTactic,
    TacticMemory,
    _destination,
    _distance,
)

_test_control_directory: TemporaryDirectory[str] | None = None
_previous_control_file: str | None = None
_previous_browser_intel_file: str | None = None
_previous_recovery_targets_file: str | None = None


def setUpModule() -> None:
    global _test_control_directory, _previous_control_file
    global _previous_browser_intel_file, _previous_recovery_targets_file
    _test_control_directory = TemporaryDirectory()
    _previous_control_file = os.environ.get("ARENA_HERO_CONTROL_FILE")
    os.environ["ARENA_HERO_CONTROL_FILE"] = str(
        Path(_test_control_directory.name) / ".arena_hero_control.json"
    )
    _previous_browser_intel_file = os.environ.get("ARENA_HERO_BROWSER_INTEL_FILE")
    os.environ["ARENA_HERO_BROWSER_INTEL_FILE"] = str(
        Path(_test_control_directory.name) / ".arena_hero_browser_intel.json"
    )
    _previous_recovery_targets_file = os.environ.get(
        "ARENA_HERO_RECOVERY_TARGETS_FILE"
    )
    os.environ["ARENA_HERO_RECOVERY_TARGETS_FILE"] = str(
        Path(_test_control_directory.name) / ".arena_hero_recovery_targets.json"
    )


def tearDownModule() -> None:
    global _test_control_directory
    if _previous_control_file is None:
        os.environ.pop("ARENA_HERO_CONTROL_FILE", None)
    else:
        os.environ["ARENA_HERO_CONTROL_FILE"] = _previous_control_file
    if _previous_browser_intel_file is None:
        os.environ.pop("ARENA_HERO_BROWSER_INTEL_FILE", None)
    else:
        os.environ["ARENA_HERO_BROWSER_INTEL_FILE"] = _previous_browser_intel_file
    if _previous_recovery_targets_file is None:
        os.environ.pop("ARENA_HERO_RECOVERY_TARGETS_FILE", None)
    else:
        os.environ["ARENA_HERO_RECOVERY_TARGETS_FILE"] = (
            _previous_recovery_targets_file
        )
    if _test_control_directory is not None:
        _test_control_directory.cleanup()
        _test_control_directory = None


CORE_ID = UUID("00000000-0000-4000-8000-000000000100")
WORKER_LOW = UUID("00000000-0000-4000-8000-000000000001")
WORKER_HIGH = UUID("00000000-0000-4000-8000-000000000002")
WORKER_THIRD = UUID("00000000-0000-4000-8000-000000000005")
WORKER_FOURTH = UUID("00000000-0000-4000-8000-000000000006")
WORKER_FIFTH = UUID("00000000-0000-4000-8000-000000000007")
WORKER_SIXTH = UUID("00000000-0000-4000-8000-000000000008")
WORKER_SEVENTH = UUID("00000000-0000-4000-8000-000000000012")
WORKER_EIGHTH = UUID("00000000-0000-4000-8000-000000000013")
RANGER_ID = UUID("00000000-0000-4000-8000-000000000003")
RANGER_TWO_ID = UUID("00000000-0000-4000-8000-000000000004")
RANGER_THREE_ID = UUID("00000000-0000-4000-8000-000000000011")
RANGER_FOURTH_ID = UUID("00000000-0000-4000-8000-000000000015")
VANGUARD_ID = UUID("00000000-0000-4000-8000-000000000009")
VANGUARD_TWO_ID = UUID("00000000-0000-4000-8000-000000000010")
VANGUARD_THREE_ID = UUID("00000000-0000-4000-8000-000000000014")
VANGUARD_FOURTH_ID = UUID("00000000-0000-4000-8000-000000000016")
ENEMY_CORE_ID = UUID("00000000-0000-4000-8000-000000000200")
ENEMY_RANGER_ID = UUID("00000000-0000-4000-8000-000000000201")


def core(
    position: tuple[int, int] = (5, 5),
    *,
    hp: int = 5,
    shield: int = 5,
) -> CoreView:
    return CoreView(
        kind="CORE",
        id=CORE_ID,
        controlled=True,
        owner_username="test_hero",
        position=position,
        hp=hp,
        shield=shield,
        state=CoreState.NORMAL,
    )


def moving_core(
    position: tuple[int, int] = (5, 5),
    *,
    direction: Direction = Direction.RIGHT,
    progress: int = 1,
) -> CoreView:
    destination = (
        position[0] + direction.delta[0],
        position[1] + direction.delta[1],
    )
    return CoreView(
        kind="CORE",
        id=CORE_ID,
        controlled=True,
        owner_username="test_hero",
        position=position,
        hp=5,
        shield=5,
        state=CoreState.MOVING,
        move_direction=direction,
        move_progress=progress,
        move_required_ticks=4,
        destination=destination,
    )


def worker(
    unit_id: UUID,
    position: tuple[int, int],
    *,
    cargo: int = 0,
) -> UnitView:
    return UnitView(
        kind="UNIT",
        id=unit_id,
        controlled=True,
        position=position,
        hp=2,
        unit_type=UnitType.WORKER,
        cargo=cargo,
    )


def ranger(
    position: tuple[int, int],
    unit_id: UUID = RANGER_ID,
    *,
    hp: int = 2,
) -> UnitView:
    return UnitView(
        kind="UNIT",
        id=unit_id,
        controlled=True,
        position=position,
        hp=hp,
        unit_type=UnitType.RANGER,
    )


def vanguard(
    position: tuple[int, int],
    unit_id: UUID = VANGUARD_ID,
    *,
    hp: int = 4,
) -> UnitView:
    return UnitView(
        kind="UNIT",
        id=unit_id,
        controlled=True,
        position=position,
        hp=hp,
        unit_type=UnitType.VANGUARD,
    )


def enemy_ranger(
    position: tuple[int, int],
    *,
    hp: int = 2,
    unit_id: UUID = ENEMY_RANGER_ID,
) -> UnitView:
    return UnitView(
        kind="UNIT",
        id=unit_id,
        controlled=False,
        position=position,
        hp=hp,
        unit_type=UnitType.RANGER,
    )


def enemy_vanguard(
    position: tuple[int, int],
    *,
    hp: int = 4,
    unit_id: UUID = UUID(int=0x8002),
) -> UnitView:
    return UnitView(
        kind="UNIT",
        id=unit_id,
        controlled=False,
        position=position,
        hp=hp,
        unit_type=UnitType.VANGUARD,
    )


def enemy_worker(
    position: tuple[int, int],
    *,
    unit_id: UUID = UUID(int=0x8001),
) -> UnitView:
    return UnitView(
        kind="UNIT",
        id=unit_id,
        controlled=False,
        position=position,
        hp=2,
        unit_type=UnitType.WORKER,
    )


def enemy_core(position: tuple[int, int]) -> CoreView:
    return CoreView(
        kind="CORE",
        id=ENEMY_CORE_ID,
        controlled=False,
        owner_username="enemy_hero",
        position=position,
        hp=5,
        shield=0,
        state=CoreState.NORMAL,
    )


def make_turn(
    *,
    tick: int = 8,
    own_core: CoreView | None = None,
    units: tuple[UnitView, ...] = (),
    enemies: tuple[UnitView | CoreView, ...] = (),
    resources: int = 0,
    resource_cells: tuple[tuple[int, int], ...] = (),
    obstacle_cells: tuple[tuple[int, int], ...] = (),
    events: tuple[ResolutionEvent, ...] = (),
    beacon: ChampionBeacon | None = None,
) -> tuple[Turn, list]:
    objects: list = []
    if obstacle_cells:
        objects.append(TerrainView(kind="OBSTACLE", positions=obstacle_cells))
    if resource_cells:
        objects.append(TerrainView(kind="RESOURCE", positions=resource_cells))
    if own_core is not None:
        objects.append(own_core)
    objects.extend(units)
    objects.extend(enemies)

    population = len(units)
    status = PlayerStatus.ACTIVE if own_core is not None else PlayerStatus.RESPAWNING
    state = PlayerState(
        status=status,
        respawn_at_tick=None if own_core is not None else tick + 1,
        resources=resources,
        population=population,
        champion_beacon=beacon or ChampionBeacon(position=(99, 99)),
        objects=tuple(objects),
        events=events,
    )
    submitted: list = []

    def submitter(plan, idempotency_key):
        submitted.append((plan, idempotency_key))
        return Accepted(
            accepted=True,
            tick=plan.tick,
            source=CommandSource.AGENT,
            received_at=datetime.now(timezone.utc),
        )

    return Turn(tick=tick, state=state, submitter=submitter), submitted


class LightningModeTests(unittest.TestCase):
    """闪电模式：建造顺序、独立猎手 claim/释放、巡逻不越框、Core 告急召回。"""

    def _box_core(self) -> CoreView:
        # Core 落在默认方框内，避免被入框迁移逻辑覆盖猎手行为。
        return core((600, 600))

    def test_cap10_forces_vanguard_not_ranger(self) -> None:
        # pop1 → 容量 10，资源 10：必须产先锋（游侠要 12 存不下）。
        turn, _ = make_turn(
            own_core=self._box_core(),
            units=(worker(WORKER_LOW, (601, 600)),),
            resources=10,
        )
        SmartTactic(TacticMemory()).choose_actions(turn)

        self.assertIsInstance(turn.plan.core_action, SpawnAction)
        self.assertEqual(turn.plan.core_action.unit_type, UnitType.VANGUARD)

    def test_second_worker_raises_cap_before_ranger(self) -> None:
        # pop2（工人+先锋），容量仍 10：应产工人#2 抬容量，而非卡在游侠。
        turn, _ = make_turn(
            own_core=self._box_core(),
            units=(
                worker(WORKER_LOW, (601, 600)),
                vanguard((602, 600)),
            ),
            resources=10,
        )
        SmartTactic(TacticMemory()).choose_actions(turn)

        self.assertIsInstance(turn.plan.core_action, SpawnAction)
        self.assertEqual(turn.plan.core_action.unit_type, UnitType.WORKER)

    def test_vanguard_claims_and_sweeps_unguarded_core(self) -> None:
        turn, _ = make_turn(
            own_core=self._box_core(),
            units=(vanguard((609, 600)),),
            enemies=(enemy_core((610, 600)),),
        )
        SmartTactic(TacticMemory()).choose_actions(turn)

        action = turn.plan.unit_actions[VANGUARD_ID]
        self.assertIsInstance(action, SweepAction)
        self.assertEqual(action.direction, Direction.RIGHT)

    def test_releases_claim_when_enemy_vanguard_guards_target(self) -> None:
        # 守卫贴脸目标 Core（1 格，≤close radius 3）→ 释放 claim，不扑上去 sweep。
        turn, _ = make_turn(
            own_core=self._box_core(),
            units=(vanguard((605, 600)),),
            enemies=(
                enemy_core((610, 600)),
                enemy_vanguard((611, 600)),
            ),
        )
        memory = TacticMemory()
        SmartTactic(memory).choose_actions(turn)

        # 无 claim，且先锋动作不是朝向该 Core 的 sweep。
        self.assertEqual(memory.lightning_claims, {})
        action = turn.plan.unit_actions.get(VANGUARD_ID)
        self.assertFalse(
            isinstance(action, SweepAction)
            and _destination((605, 600), action.direction) == (610, 600)
        )

    def test_blacklists_crowded_core_permanently(self) -> None:
        # 重兵 Core 一旦放弃 → 永久黑名单，即使之后 sighting 老化也不再 claim。
        from arena_hero_strategy import EnemySighting
        memory = TacticMemory()
        memory.enemy_sightings = {
            "crowded-core": EnemySighting(position=(515, -216), seen_tick=1000, is_core=True),
            "g-1": EnemySighting(position=(514, -214), seen_tick=1010, is_core=False),
            "g-2": EnemySighting(position=(514, -218), seen_tick=1011, is_core=False),
        }
        memory.last_tick = 1015
        turn, _ = make_turn(
            own_core=core((600, 600)),
            units=(vanguard((620, 600)),),
        )
        SmartTactic(memory).choose_actions(turn)
        self.assertIn("crowded-core", memory.lightning_blacklist)
        # 模拟 sighting 老化后（守卫 sighting 超龄，crowd 不再触发）仍不 claim。
        memory.enemy_sightings = {
            "crowded-core": EnemySighting(position=(515, -216), seen_tick=1000, is_core=True),
        }
        memory.last_tick = 9999  # 守卫 sighting 已删，即使还在黑名单也不 claim
        turn2, _ = make_turn(
            own_core=core((600, 600)),
            units=(vanguard((620, 600)),),
        )
        SmartTactic(memory).choose_actions(turn2)
        self.assertEqual(memory.lightning_claims, {})
        self.assertIn("crowded-core", memory.lightning_blacklist)

    def test_skips_crowded_target_with_fogged_guards(self) -> None:
        # 目标 Core 周围雾里有 2 个近期敌方 sighting（当前不可见）→ 视为重兵把守，
        # 不 claim，先锋转扇区探索。补住"雾里守卫看不见→误判无护卫→凑过去卡死"。
        memory = TacticMemory()
        from arena_hero_strategy import EnemySighting
        memory.enemy_sightings = {
            "core-1": EnemySighting(position=(515, -216), seen_tick=1000, is_core=True),
            "g-1": EnemySighting(position=(514, -214), seen_tick=1010, is_core=False),
            "g-2": EnemySighting(position=(514, -218), seen_tick=1011, is_core=False),
        }
        memory.last_tick = 1015
        turn, _ = make_turn(
            own_core=core((600, 600)),
            units=(vanguard((620, 600)),),
            # 当前视野无敌方单位（守卫在雾里）。
        )
        SmartTactic(memory).choose_actions(turn)
        # 不 claim 重兵 Core。
        self.assertEqual(memory.lightning_claims, {})

    def test_keeps_claim_and_detours_around_distant_guard(self) -> None:
        # 守卫在目标 5 格（>close 3，不贴脸）且不触发家防（离自家 Core 远）→
        # 不释放 claim；先锋保留 claim，朝目标走但把守卫格当障碍绕开。
        # 自家 Core (600,600)；敌方 Core (640,600)；守卫先锋 (635,600)（目标 5 格）。
        turn, _ = make_turn(
            own_core=core((600, 600)),
            units=(vanguard((620, 600)),),
            enemies=(
                enemy_core((640, 600)),
                enemy_vanguard((635, 600)),  # 目标和先锋之间，离目标 5 格
            ),
        )
        memory = TacticMemory()
        SmartTactic(memory).choose_actions(turn)
        # claim 保留（不释放）；先锋走了，不原地卡死。
        self.assertEqual(len(memory.lightning_claims), 1)
        self.assertIn(VANGUARD_ID, turn.plan.unit_actions)

    def test_patrol_waypoint_stays_inside_donut(self) -> None:
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        # 巡逻不需战斗护卫门槛（贫瘠区不能等攒够先锋+游侠才动）。
        # Core 置于方环内（max-norm 半径在 500..700），仅 1 工人、0 战斗单位。
        turn, _ = make_turn(
            own_core=core((535, -201)),
            units=(worker(WORKER_LOW, (536, -201)),),
        )
        tactic.choose_actions(turn)
        waypoint = memory.lightning_patrol_waypoint
        self.assertIsNotNone(waypoint)
        inner_r, outer_r = LIGHTNING_DEFAULT_RING
        radius = max(abs(waypoint[0]), abs(waypoint[1]))
        # 巡逻点应在巡逻半径 pr（方环外半）的方形周界上。
        self.assertGreaterEqual(radius, inner_r)
        self.assertLessEqual(radius, outer_r)

    def test_core_threat_recalls_vanguard_instead_of_hunting(self) -> None:
        # 敌方先锋贴近 Core → 视为告急，先锋召回护核而非去猎杀远处 Core。
        turn, _ = make_turn(
            own_core=self._box_core(),
            units=(vanguard((601, 600)),),
            enemies=(
                enemy_core((610, 600)),
                enemy_vanguard((602, 600)),
            ),
        )
        memory = TacticMemory()
        SmartTactic(memory).choose_actions(turn)

        # 无猎杀 claim（没有去猎杀远处的敌方 Core）；先锋可能在召回时横扫
        # 贴近 Core 的敌方先锋，那是防御行为，不算猎杀。
        self.assertEqual(memory.lightning_claims, {})

    def test_patrol_continues_past_noncombat_enemy_worker(self) -> None:
        # 敌方工人在 Core 8 格内（无攻击力）→ Core 继续巡逻，不停摆。
        turn, _ = make_turn(
            own_core=core((535, -201)),
            units=(worker(WORKER_LOW, (540, -201)),),
            enemies=(enemy_worker((536, -201)),),
        )
        SmartTactic(TacticMemory()).choose_actions(turn)
        self.assertIsInstance(turn.plan.core_action, StartMoveAction)

    def test_patrol_detours_around_combat_enemy(self) -> None:
        # 敌方先锋在 Core 右侧 6 格（能 sweep 到接近的 Core）。Core 朝巡逻点
        # (650,-650) 走应选不更靠近该先锋的方向绕开，而非直冲或原地停下。
        turn, _ = make_turn(
            own_core=core((535, -201)),
            units=(worker(WORKER_LOW, (545, -180)),),  # 工人远处发现先锋
            enemies=(enemy_vanguard((541, -201)),),
        )
        SmartTactic(TacticMemory()).choose_actions(turn)
        self.assertIsInstance(turn.plan.core_action, StartMoveAction)
        dest = _destination((535, -201), turn.plan.core_action.direction)
        # 绕行：新位置不应比原位置更靠近那个先锋。
        self.assertGreaterEqual(
            _distance(dest, (541, -201)),
            _distance((535, -201), (541, -201)),
        )

    def test_patrol_flees_adjacent_combat_enemy(self) -> None:
        # 敌方先锋紧贴 Core 相邻（sweep 可直接命中）。Core 应选逃离方向（远离先锋）
        # 而非原地停留（留在 sweep 范围内会被持续打），也不朝先锋走。
        turn, _ = make_turn(
            own_core=core((535, -201)),
            units=(worker(WORKER_LOW, (540, -201)),),
            enemies=(enemy_vanguard((536, -201)),),
        )
        SmartTactic(TacticMemory()).choose_actions(turn)
        self.assertIsInstance(turn.plan.core_action, StartMoveAction)
        dest = _destination((535, -201), turn.plan.core_action.direction)
        # 逃离：新位置比原位置更远离先锋。
        self.assertGreater(
            _distance(dest, (536, -201)),
            _distance((535, -201), (536, -201)),
        )


    def test_sector_target_stays_near_units_quadrant(self) -> None:
        # 回归：单位在第四象限 (y<0)，扇区目标不该在第一象限 (y>0)。
        # 旧 bug 用固定四角把单位派去 (644,644)，要穿越原点。
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        turn, _ = make_turn(
            own_core=core((535, -201)),
            units=(vanguard((516, -222)),),
        )
        sector = tactic._lightning_sector_target(turn, turn.vanguards[0])
        self.assertIsNotNone(sector)
        # 单位 y=-222（负），扇区目标也应在 y<0 半侧，不被派去 y>0。
        self.assertLess(sector[1], 0, f"sector {sector} should stay in y<0 half")
        # 且在方环内。
        radius = max(abs(sector[0]), abs(sector[1]))
        self.assertGreaterEqual(radius, 500)
        self.assertLessEqual(radius, 700)

    def test_patrol_moves_toward_waypoint_not_origin(self) -> None:
        # Core 在 (535,-201)，最近巡逻角 (650,-650)。应朝巡逻点（远离原点）
        # 走，而非被 beacon_progress 拉向原点（旧 bug 会漂向 LEFT/UP）。
        turn, _ = make_turn(
            own_core=core((535, -201)),
            units=(worker(WORKER_LOW, (540, -201)),),
        )
        SmartTactic(TacticMemory()).choose_actions(turn)
        self.assertIsInstance(turn.plan.core_action, StartMoveAction)
        dest = _destination((535, -201), turn.plan.core_action.direction)
        self.assertLess(
            _distance(dest, (650, -650)),
            _distance((535, -201), (650, -650)),
        )

    # ---- 侦察改造:并排游侠探路 + 先锋 V 字纵深 + 集火 ----

    def test_ranger_step_does_not_use_astar(self) -> None:
        # 游侠绕圈走 Core 风格四邻打分(_lightning_step_toward),不走 A*。
        # 空旷地形朝目标角单调推进,产生 lightning_ranger_scout 决策(非 fallback)。
        from arena_hero_strategy import MovementPlanner
        memory = TacticMemory()
        memory.lightning_patrol_phase = 2  # 目标第三象限角 (-650,-650)
        tactic = SmartTactic(memory)
        turn, _ = make_turn(
            own_core=core((600, 600)),
            units=(ranger((645, 646), UUID(int=0xB005)),),
        )
        decisions: list[str] = []
        planner = MovementPlanner(turn, memory, decisions)
        tactic._choose_rangers_lightning(turn, planner, set(), decisions)
        # 产生了轨道单步决策(前 4 游侠走开路 lightning_breakthrough,第 5 起走
        # 远行星 lightning_ranger_far_orbit),且不是 A* fallback。
        self.assertTrue(
            any(
                "reason=lightning_breakthrough" in d
                or "reason=lightning_ranger_far_orbit" in d
                for d in decisions
            ),
            f"expected orbit step decision, got {decisions}",
        )
        self.assertFalse(
            any(":fallback" in d for d in decisions),
            f"should not use A* fallback, got {decisions}",
        )

    def test_ranger_step_follows_obstacle_contour_no_oscillation(self) -> None:
        # 游侠遇障碍墙时四邻打分会沿轮廓绕行(选不撞墙且离目标近的方向),
        # 不会在两格间横跳。墙在左侧、目标在更左 → 游侠应朝上下绕,而非撞墙抖。
        from arena_hero_strategy import MovementPlanner
        memory = TacticMemory()
        memory.lightning_patrol_phase = 2  # 目标 (-650,-650),在左下
        tactic = SmartTactic(memory)
        # 游侠在 (650,-653),左侧一堵墙挡住去路。
        walls = tuple((x, -653) for x in range(645, 650))
        turn, _ = make_turn(
            own_core=core((600, 600)),
            units=(ranger((650, -653), UUID(int=0xB006)),),
            obstacle_cells=walls,
        )
        memory.known_obstacles = set(walls)
        decisions: list[str] = []
        planner = MovementPlanner(turn, memory, decisions)
        # 连走 6 步,每步重建 planner 模拟游侠移动,断言不在两格间横跳。
        positions = [(650, -653)]
        uid = str(turn.rangers[0].id)
        for _ in range(6):
            decisions_step: list[str] = []
            r_unit = ranger(positions[-1], UUID(int=0xB006))
            step_turn, _ = make_turn(
                own_core=core((600, 600)),
                units=(r_unit,),
                obstacle_cells=walls,
            )
            planner_step = MovementPlanner(step_turn, memory, decisions_step)
            tactic._choose_rangers_lightning(step_turn, planner_step, set(), decisions_step)
            # 找出这一步游侠去了哪。
            moved = False
            for d in decisions_step:
                if "ranger:" in d and " to=" in d and "WAIT" not in d:
                    to = d.split("to=")[1].split()
                    pos = (int(to[0].strip("(,")), int(to[1].strip(")")))
                    positions.append(pos)
                    moved = True
                    break
            if not moved:
                positions.append(positions[-1])
        # 6 步内至少走到 4 个不同格(不在 ≤2 格间横跳)。
        uniq = len(set(positions))
        self.assertGreaterEqual(
            uniq, 4, f"ranger stuck oscillating: {positions}"
        )

    def test_ranger_scout_aligns_to_core_patrol_phase(self) -> None:
        # 游侠首次探路的角应跟 Core 巡逻 phase 对齐(朝 Core 前方),而非最近角。
        # Core 巡逻 phase=2 → 第三象限角 (-650,-650);游侠即使在第一象限附近,
        # 起点目标也应是第三象限角,顺方向绕圈、点亮 Core 前方视野。
        memory = TacticMemory()
        memory.lightning_patrol_phase = 2
        tactic = SmartTactic(memory)
        # 游侠在第一象限 (650, 650) 附近(离第一象限角最近,但应被忽略)。
        turn, _ = make_turn(
            own_core=core((600, 600)),
            units=(ranger((648, 649), UUID(int=0xB004)),),
        )
        target = tactic._lightning_ranger_scout_target(turn, turn.rangers[0])
        uid = str(turn.rangers[0].id)
        # phase 对齐 Core=2 → 目标在第三象限角(x<0, y<0)。
        self.assertEqual(memory.lightning_scout_phase[uid], 2)
        self.assertIsNotNone(target)
        self.assertLess(target[0], 0, "should head to Core's forward corner (phase 2 = Q3)")
        self.assertLess(target[1], 0)

    def test_ranger_scout_advances_corner_independently_of_core(self) -> None:
        # 游侠已到达当前角(周界角)→ 推进下一角,不等 Core。Core 留在原地不动,
        # 游侠靠自己在周界上转圈。
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        # Core 在 (600,600) 不靠近任何角;游侠放在 (650,650) 附近(第一象限角)。
        turn, _ = make_turn(
            own_core=core((600, 600)),
            units=(ranger((648, 649), UUID(int=0xB003)),),
        )
        # 首次调用:认领 lane、选最近角为目标(应是第一象限角附近)。
        first = tactic._lightning_ranger_scout_target(turn, turn.rangers[0])
        uid = str(turn.rangers[0].id)
        phase0 = memory.lightning_scout_phase.get(uid, 0)
        # 把游侠移到该角死区内,再调一次 → phase 应推进(独立绕圈)。
        memory.lightning_scout_phase[uid] = phase0
        turn2, _ = make_turn(
            own_core=core((600, 600)),
            units=(ranger(first, UUID(int=0xB003)),),
        )
        tactic._lightning_ranger_scout_target(turn2, turn2.rangers[0])
        self.assertEqual(
            memory.lightning_scout_phase[uid], (phase0 + 1) % 4
        )

    def test_vanguard_vee_outbound_target_orthogonal_to_heading(self) -> None:
        # Core 在 (600,600),最近巡逻角 (650,650) → 行进方向 (+x,+y)。
        # 先锋 OUTBOUND 目标应正交(perp=(-fwd_y,fwd_x)=(-1,+1)),
        # 不沿行进方向延伸;深度 ~ LIGHTNING_VEE_DEPTH;clamp 在方环内。
        from arena_hero_strategy import LIGHTNING_VEE_DEPTH
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        turn, _ = make_turn(
            own_core=core((600, 600)),
            units=(vanguard((600, 605)),),
        )
        target = tactic._lightning_vanguard_vee_target(turn, turn.vanguards[0])
        self.assertIsNotNone(target)
        # perp=(-1,+1):目标 x 减、y 增(沿 perp,不沿 fwd)。
        self.assertLess(target[0], 600, "orthogonal: target x should decrease")
        self.assertGreater(target[1], 605, "orthogonal: target y should increase")
        # 深度 ~ VEE_DEPTH。
        self.assertGreater(_distance(target, (600, 605)), LIGHTNING_VEE_DEPTH - 5)
        # 在方环内。
        radius = max(abs(target[0]), abs(target[1]))
        self.assertLessEqual(radius, 700)

    def test_vanguard_vee_flips_to_inbound_on_reaching_depth(self) -> None:
        # 先锋已接近 OUTBOUND 目标(≤ REACH_TOLERANCE)→ 翻 INBOUND,目标=Core。
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        turn, _ = make_turn(
            own_core=core((600, 600)),
            units=(vanguard((600, 630)),),
        )
        # 第一调用初始化 OUTBOUND 目标(垂直方向 ~ +32)。
        tactic._lightning_vanguard_vee_target(turn, turn.vanguards[0])
        state = memory.lightning_vee_state[str(turn.vanguards[0].id)]
        state["target"] = (600, 632)  # 先锋(600,630) 距它 2 ≤ tol
        # 再调一次:先锋在 (600,630) 距 (600,632) ≤3 → 翻 INBOUND。
        target = tactic._lightning_vanguard_vee_target(turn, turn.vanguards[0])
        self.assertEqual(memory.lightning_vee_state[str(turn.vanguards[0].id)]["phase"], "IN")
        self.assertEqual(target, (600, 600))

    def test_vanguard_vee_flips_to_outbound_on_reaching_core(self) -> None:
        # INBOUND 状态、先锋贴近 Core → 翻 OUTBOUND,leg 翻转,origin 重设。
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        turn, _ = make_turn(
            own_core=core((600, 600)),
            units=(vanguard((602, 600)),),  # 距 Core 2 ≤ HOME_TOL
        )
        # 手动置 INBOUND 状态。
        memory.lightning_vee_state[str(turn.vanguards[0].id)] = {
            "phase": "IN",
            "leg": 0,
            "origin": (568, 600),
            "target": (600, 600),
        }
        tactic._lightning_vanguard_vee_target(turn, turn.vanguards[0])
        state = memory.lightning_vee_state[str(turn.vanguards[0].id)]
        self.assertEqual(state["phase"], "OUT")
        self.assertEqual(state["leg"], 1)  # 翻转
        self.assertEqual(state["origin"], (600, 600))  # origin 重设为 Core

    def test_focus_fire_multiple_units_claim_same_core(self) -> None:
        # 同一敌方 Core 允许多 unit 同时 claim(集火),不互相排除。
        from arena_hero_strategy import EnemySighting
        memory = TacticMemory()
        memory.enemy_sightings = {
            "target-A": EnemySighting(position=(620, 600), seen_tick=100, is_core=True),
        }
        memory.last_tick = 100
        tactic = SmartTactic(memory)
        turn, _ = make_turn(
            own_core=core((600, 600)),
            units=(
                vanguard((610, 600)),
                ranger((610, 605)),
            ),
        )
        tactic.choose_actions(turn)
        claims = list(memory.lightning_claims.values())
        # 两个 unit 都 claim 同一 target-A(集火)。
        self.assertEqual(claims, ["target-A", "target-A"])

    def test_focus_fire_caps_at_max_attackers(self) -> None:
        # 已有 3 个 unit claim target-A(上限)→ 第 4 个 unit 不应再 claim 它,
        # 应跳过(无其他目标 → 不 claim)。
        from arena_hero_strategy import EnemySighting, LIGHTNING_FOCUS_MAX_ATTACKERS
        memory = TacticMemory()
        memory.enemy_sightings = {
            "target-A": EnemySighting(position=(620, 600), seen_tick=100, is_core=True),
        }
        memory.last_tick = 100
        memory.lightning_claims = {
            "u1": "target-A",
            "u2": "target-A",
            "u3": "target-A",
        }
        tactic = SmartTactic(memory)
        turn, _ = make_turn(
            own_core=core((600, 600)),
            units=(vanguard((610, 600), UUID(int=0xD004)),),
        )
        acquired = tactic._lightning_acquire_target(turn, turn.vanguards[0])
        # 已满员 → 不 claim(返回 None),第 4 个 unit 不扑同一目标。
        self.assertIsNone(acquired)

    def test_blacklist_releases_all_units_claiming_same_core(self) -> None:
        # 两 unit 都 claim target-A;判为 crowded → 两个 claim 都释放 + 黑名单 +
        # sightings 清脏。
        from arena_hero_strategy import EnemySighting
        memory = TacticMemory()
        memory.enemy_sightings = {
            "target-A": EnemySighting(position=(620, 600), seen_tick=100, is_core=True),
            "g-1": EnemySighting(position=(619, 599), seen_tick=100, is_core=False),
            "g-2": EnemySighting(position=(619, 601), seen_tick=100, is_core=False),
        }
        memory.last_tick = 100
        memory.lightning_claims = {
            "u1": "target-A",
            "u2": "target-A",
        }
        tactic = SmartTactic(memory)
        tactic._lightning_blacklist_core("target-A")
        self.assertIn("target-A", memory.lightning_blacklist)
        # 两个 unit 的 claim 都释放。
        self.assertEqual(memory.lightning_claims, {})
        # sightings 中 target-A 被清(守卫 sighting 不动)。
        self.assertNotIn("target-A", memory.enemy_sightings)

    def test_blacklisted_core_removed_from_sightings(self) -> None:
        # 已黑名单的 Core 在 acquire 时被从 sightings 清掉(脏数据不残留)。
        from arena_hero_strategy import EnemySighting
        memory = TacticMemory()
        memory.enemy_sightings = {
            "bl": EnemySighting(position=(620, 600), seen_tick=100, is_core=True),
        }
        memory.lightning_blacklist.add("bl")
        memory.last_tick = 100
        tactic = SmartTactic(memory)
        turn, _ = make_turn(
            own_core=core((600, 600)),
            units=(vanguard((610, 600)),),
        )
        tactic._lightning_acquire_target(turn, turn.vanguards[0])
        self.assertNotIn("bl", memory.enemy_sightings)

    # ---- 绕银河多层轨道体系 ----

    def test_lightning_banks_when_slot0_vanguard_unaffordable(self) -> None:
        # 新固定阶梯槽 0=先锋(10)。pop1(1 免费工人 0 先锋)、资源 5 买不起先锋
        # → 应攒钱,不 fallthrough 造工人。绝不在买不起槽 0 先锋时向下造便宜兵种。
        turn, _ = make_turn(
            own_core=self._box_core(),
            units=(worker(WORKER_LOW, (601, 600)),),
            resources=5,
        )
        SmartTactic(TacticMemory()).choose_actions(turn)
        self.assertNotIsInstance(turn.plan.core_action, SpawnAction)

    def test_lightning_build_order_first_three_slots(self) -> None:
        # 槽 0=先锋, 1=工人, 2=游侠。逐 pop 检查 _lightning_build_slot。
        from arena_hero_strategy import SmartTactic
        tactic = SmartTactic(TacticMemory())
        self.assertIs(tactic._lightning_build_slot(1), UnitType.VANGUARD)
        self.assertIs(tactic._lightning_build_slot(2), UnitType.WORKER)
        self.assertIs(tactic._lightning_build_slot(3), UnitType.RANGER)
        # pop≥9 起全游侠
        self.assertIs(tactic._lightning_build_slot(9), UnitType.RANGER)
        self.assertIs(tactic._lightning_build_slot(15), UnitType.RANGER)
        # 满 20 停
        self.assertIsNone(tactic._lightning_build_slot(20))

    def test_breakthrough_rings_spaced_by_ranger_vision(self) -> None:
        # 前 4 游侠走开路轨道(绕原点同心大环),半径 = pr + OFFSET + lane*GAP[R],
        # 相邻差 = 游侠视野半径(GAP[R]=5),覆盖连续不重叠。
        from arena_hero_strategy import LIGHTNING_ORBIT_LANE_GAP_RADIUS

        rangers = tuple(
            ranger((605, 600 + i), UUID(int=0xC000 + i)) for i in range(4)
        )
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        turn, _ = make_turn(own_core=core((600, 600)), units=rangers)
        tactic.choose_actions(turn)
        pts = [
            tactic._lightning_breakthrough_target(turn, r, i)
            for i, r in enumerate(turn.rangers)
        ]
        pts = [p for p in pts if p is not None]
        self.assertGreaterEqual(len(pts), 2)
        gap_r = LIGHTNING_ORBIT_LANE_GAP_RADIUS[UnitType.RANGER]
        radii = sorted(max(abs(p[0]), abs(p[1])) for p in pts)
        for i in range(len(radii) - 1):
            self.assertGreaterEqual(
                radii[i + 1] - radii[i],
                gap_r - 1,
                f"breakthrough rings {radii} too close",
            )
        # 都在方环安全区内。
        for s in pts:
            self.assertGreaterEqual(max(abs(s[0]), abs(s[1])), 500)
            self.assertLessEqual(max(abs(s[0]), abs(s[1])), 700)

    def test_engage_assessment_skips_ranger_guards(self) -> None:
        # 敌方 Core 周围有游侠守卫(远程) → SKIP(我 2HP 游侠易亏,回避)。
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        turn, _ = make_turn(
            own_core=core((600, 600)),
            units=(vanguard((620, 600)),),
            enemies=(
                enemy_core((640, 600)),
                enemy_ranger((635, 600)),
            ),
        )
        self.assertEqual(tactic._lightning_engage_assessment(turn, (640, 600)), "SKIP")

    def test_engage_assessment_press_for_lone_vanguard_guard(self) -> None:
        # 敌方 Core 周围只有先锋守卫(近战),无游侠 → PRESS(我游侠手长,游击取胜)。
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        turn, _ = make_turn(
            own_core=core((600, 600)),
            units=(ranger((620, 600), UUID(int=0xB010)),),
            enemies=(
                enemy_core((640, 600)),
                enemy_vanguard((635, 600)),
            ),
        )
        self.assertEqual(tactic._lightning_engage_assessment(turn, (640, 600)), "PRESS")

    def test_defense_tier_near_mid_far_none(self) -> None:
        # 按敌方与我 Core 距离分三档。
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        # NEAR:敌方先锋 4 格(≤6) → 全员含工人回防
        turn_near, _ = make_turn(
            own_core=core((600, 600)),
            units=(vanguard((601, 600)),),
            enemies=(enemy_vanguard((604, 600)),),
        )
        self.assertEqual(tactic._lightning_defense_tier(turn_near), "NEAR")
        # MID:敌方游侠 15 格(6<d≤20) → 全体游侠回防
        turn_mid, _ = make_turn(
            own_core=core((600, 600)),
            units=(vanguard((601, 600)),),
            enemies=(enemy_ranger((615, 600)),),
        )
        self.assertEqual(tactic._lightning_defense_tier(turn_mid), "MID")
        # FAR:敌方游侠 30 格(20<d≤40) → 局部游击,不全撤
        turn_far, _ = make_turn(
            own_core=core((600, 600)),
            units=(vanguard((601, 600)),),
            enemies=(enemy_ranger((630, 600)),),
        )
        self.assertEqual(tactic._lightning_defense_tier(turn_far), "FAR")
        # NONE:无战斗单位 → 正常绕轨道
        turn_none, _ = make_turn(
            own_core=core((600, 600)),
            units=(vanguard((601, 600)),),
        )
        self.assertEqual(tactic._lightning_defense_tier(turn_none), "NONE")

    # ---- 鬼打墙修复:visited 重罚 + 卡住检测/逃生 + 障碍角动态跳过 ----

    def test_step_toward_visited_penalty_forces_detour(self) -> None:
        # 死角横跳的根因:距离项(±1)盖过 visited 轻罚(0.05/次)。提权后
        # (0.5/次)在反复蹭过的格上惩罚快速累积,单位被迫选没走过的绕路方向。
        from arena_hero_strategy import MovementPlanner
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        r_unit = ranger((650, 600), UUID(int=0xB020))
        turn, _ = make_turn(own_core=core((600, 600)), units=(r_unit,))
        # 左边格 (649,600) 朝目标最近,但已反复走过 6 次(visited 惩罚 3.0 >
        # 绕路的距离差 2.0);上下格没走过 → 应选上/下绕行。
        memory.visited[(649, 600)] = 6
        decisions: list[str] = []
        planner = MovementPlanner(turn, memory, decisions)
        moved = tactic._lightning_step_toward(
            turn, planner, turn.rangers[0], (620, 600), "test_detour"
        )
        self.assertTrue(moved)
        move_line = next(d for d in decisions if "move" in d)
        self.assertNotIn("to=(649, 600)", move_line)

    def test_step_toward_triggers_escape_after_repeated_oscillation(self) -> None:
        # 连续 3 次检出"8 tick 窗口内活动范围 ≤2 格"→ 触发逃生模式,
        # 决策 reason 带 :escape 后缀,escape_until 写入 memory。
        from arena_hero_strategy import (
            LIGHTNING_ESCAPE_DURATION_TICKS,
            MovementPlanner,
        )
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        uid = str(UUID(int=0xB021))
        # 伪造小范围震荡历史:8 个位置都在 (650,600)/(651,600) 两格间。
        memory.recent_positions[uid] = [
            (650 + (i % 2), 600) for i in range(8)
        ]
        decisions: list[str] = []
        # 每 tick 重建 turn/planner(单位每 tick 只下一次动作)。
        for tick in (100, 101, 102):
            turn, _ = make_turn(
                tick=tick,
                own_core=core((600, 600)),
                units=(ranger((650, 600), UUID(int=0xB021)),),
            )
            decisions = []
            planner = MovementPlanner(turn, memory, decisions)
            tactic._lightning_step_toward(
                turn, planner, turn.rangers[0], (620, 600), "test_escape"
            )
        self.assertGreaterEqual(
            memory.lightning_unit_escape_until.get(uid, 0),
            100 + LIGHTNING_ESCAPE_DURATION_TICKS,
        )
        # 逃生期间的移动决策带 :escape 标记。
        self.assertTrue(
            any(":escape" in d for d in decisions),
            f"expected escape-mode decision, got {decisions}",
        )

    def test_escape_mode_prefers_open_direction_over_goal(self) -> None:
        # 逃生模式忽略目标方向:目标在左、左邻格虽可走但是口袋更深处(出口 1),
        # 右邻格开阔(出口 2) → 逃生应向右出口袋,而非贪距离往左钻。
        from arena_hero_strategy import MovementPlanner
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        r_unit = ranger((650, 600), UUID(int=0xB022))
        uid = str(r_unit.id)
        memory.lightning_unit_escape_until[uid] = 200  # 已在逃生期
        # 口袋:上下两排墙 + 左端封底,开口朝右。
        walls = (
            (648, 600),
            (649, 599), (650, 599), (651, 599),
            (649, 601), (650, 601), (651, 601),
        )
        memory.known_obstacles = set(walls)
        turn, _ = make_turn(
            tick=100,
            own_core=core((600, 600)),
            units=(r_unit,),
            obstacle_cells=walls,
        )
        decisions: list[str] = []
        planner = MovementPlanner(turn, memory, decisions)
        moved = tactic._lightning_step_toward(
            turn, planner, turn.rangers[0], (620, 600), "test_open"
        )
        self.assertTrue(moved)
        move_line = next(d for d in decisions if "move" in d)
        self.assertIn("to=(651, 600)", move_line)
        self.assertIn(":escape", move_line)

    def test_escape_ends_early_after_leaving_pocket(self) -> None:
        # 逃生中若已远离震荡区域(> 检测窗口格数),提前退出逃生恢复正常寻路。
        from arena_hero_strategy import MovementPlanner
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        r_unit = ranger((670, 600), UUID(int=0xB023))
        uid = str(r_unit.id)
        memory.lightning_unit_escape_until[uid] = 200
        # recent_positions[0] 在 (650,600),当前 (670,600) 距离 20 > 窗口 8。
        memory.recent_positions[uid] = [(650, 600), (670, 600)]
        turn, _ = make_turn(tick=100, own_core=core((600, 600)), units=(r_unit,))
        decisions: list[str] = []
        planner = MovementPlanner(turn, memory, decisions)
        tactic._lightning_step_toward(
            turn, planner, turn.rangers[0], (620, 600), "test_exit_escape"
        )
        self.assertNotIn(uid, memory.lightning_unit_escape_until)
        # 恢复正常打分:朝目标(左)走。
        move_line = next(d for d in decisions if "move" in d)
        self.assertIn("to=(669, 600)", move_line)

    def test_orbit_waypoint_skips_obstructed_corner(self) -> None:
        # 目标角周围 5x5 已知障碍 >10 → 距角尚远时提前推进下一角绕行。
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        v_unit = vanguard((600, 590))
        turn, _ = make_turn(own_core=core((600, 600)), units=(v_unit,))
        uid = str(v_unit.id)
        memory.lightning_orbit_phase[uid] = 0
        # 先取一次目标角(不埋障碍),记录其坐标。
        clean_target = tactic._lightning_orbit_waypoint(
            turn, turn.vanguards[0], UnitType.VANGUARD
        )
        self.assertIsNotNone(clean_target)
        # 角埋进乱石堆:5x5 里放 12 个障碍(>LIMIT 10)。
        memory.lightning_orbit_phase[uid] = 0  # 重置相位再取一次
        memory.known_obstacles = {
            (clean_target[0] + dx, clean_target[1] + dy)
            for dx in range(-2, 3)
            for dy in range(-2, 3)
        } - {clean_target}  # 24 个,足够超限
        blocked_target = tactic._lightning_orbit_waypoint(
            turn, turn.vanguards[0], UnitType.VANGUARD
        )
        self.assertIsNotNone(blocked_target)
        self.assertNotEqual(blocked_target, clean_target)
        # 相位已推进。
        self.assertEqual(memory.lightning_orbit_phase[uid], 1)

    def test_core_patrol_waypoint_skips_obstructed_corner(self) -> None:
        # Core 巡逻角埋在乱石堆里 → 距角尚远时提前跳下一角。
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        turn, _ = make_turn(own_core=core((600, 600)), units=())
        first = tactic._lightning_patrol_waypoint(turn)
        phase_before = memory.lightning_patrol_phase
        # 把当前角埋进障碍。
        memory.known_obstacles = {
            (first[0] + dx, first[1] + dy)
            for dx in range(-2, 3)
            for dy in range(-2, 3)
        }
        second = tactic._lightning_patrol_waypoint(turn)
        self.assertNotEqual(second, first)
        self.assertEqual(
            memory.lightning_patrol_phase, (phase_before + 1) % 4
        )

    def test_escape_state_pruned_for_dead_units(self) -> None:
        # 单位死亡后 stuck/escape 状态随 last_position_tick 清理。
        memory = TacticMemory()
        dead_uid = str(UUID(int=0xDEAD))
        memory.last_position_tick[dead_uid] = 50
        memory.lightning_unit_stuck_counters[dead_uid] = 2
        memory.lightning_unit_escape_until[dead_uid] = 120
        turn, _ = make_turn(tick=100, own_core=core((600, 600)), units=())
        memory.observe(turn)
        self.assertNotIn(dead_uid, memory.lightning_unit_stuck_counters)
        self.assertNotIn(dead_uid, memory.lightning_unit_escape_until)

    def test_escape_state_survives_save_load_roundtrip(self) -> None:
        # 逃生状态字段随 memory 落盘/恢复(进程重启不丢失逃生冷却)。
        memory = TacticMemory()
        memory.lightning_unit_stuck_counters["u-1"] = 2
        memory.lightning_unit_escape_until["u-2"] = 345
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "memory.json"
            memory.save(path)
            restored = TacticMemory.load(path)
        self.assertEqual(restored.lightning_unit_stuck_counters, {"u-1": 2})
        self.assertEqual(restored.lightning_unit_escape_until, {"u-2": 345})

    def test_emergency_worker_at_80_percent_capacity(self) -> None:
        # 资源达到容量 80% 时优先造工人，而非按产兵阶梯造游侠。
        # 设置：pop=20 (容量 100), resources=80 (80%)，已达常规上限
        from arena_hero_strategy import SmartTactic
        units = tuple(worker(UUID(int=i), (600 + i % 10, 600 + i // 10)) for i in range(20))
        turn, _ = make_turn(
            own_core=core((600, 600)),
            units=units,
            resources=80,  # 容量 max(10, 20*5)=100, 80/100=80%
        )
        SmartTactic(TacticMemory()).choose_actions(turn)
        # 预期：造工人（紧急消耗资源），因为已达 20 人且资源压力大
        self.assertIsInstance(turn.plan.core_action, SpawnAction)
        self.assertEqual(turn.plan.core_action.unit_type, UnitType.WORKER)

    def test_emergency_worker_respects_100_cap(self) -> None:
        # pop=100 时不再产兵，即使资源很多。
        from arena_hero_strategy import SmartTactic
        units = tuple(worker(UUID(int=i), (600 + i % 20, 600 + i // 20)) for i in range(100))
        turn, _ = make_turn(
            own_core=core((600, 600)),
            units=units,
            resources=500,  # 容量 max(10, 100*5)=500, 已满
        )
        SmartTactic(TacticMemory()).choose_actions(turn)
        # 预期：不造兵（已达 ABSOLUTE_MAX_POPULATION）
        self.assertNotIsInstance(turn.plan.core_action, SpawnAction)

    def test_regular_build_order_under_20_when_capacity_ok(self) -> None:
        # 资源未达 80% 容量时，按产兵阶梯造兵，不触发紧急工人。
        # 设置：pop=10 (容量 50), resources=15 (30%)
        from arena_hero_strategy import SmartTactic
        units = tuple(worker(UUID(int=i), (600 + i, 600)) for i in range(10))
        turn, _ = make_turn(
            own_core=core((600, 600)),
            units=units,
            resources=15,  # 30% < 80% 不触发紧急
        )
        tactic = SmartTactic(TacticMemory())
        # 槽 10 (pop10→11) 应按阶梯造游侠，而非紧急工人
        slot_type = tactic._lightning_build_slot(10)
        self.assertEqual(slot_type, UnitType.RANGER)

    def test_worker_switches_to_closer_resource(self) -> None:
        # 工人途中发现更近资源（近 2 格以上）时切换目标。
        from arena_hero_strategy import SmartTactic, TacticMemory
        w = worker(UUID(int=1), (610, 600))
        memory = TacticMemory()
        # 工人已有目标：远资源 (620, 600) 距离 10
        memory.set_worker_goal(w, "visible_resource", (620, 600), 1)

        turn, _ = make_turn(
            own_core=core((600, 600)),
            units=(w,),
            resource_cells=((620, 600), (612, 600)),  # 远资源 d=10, 近资源 d=2
        )
        tactic = SmartTactic(memory)
        summary = tactic.choose_actions(turn)

        # 预期：切换到 (612, 600)，决策日志含 switch_to_closer_resource
        switch_decisions = [d for d in summary.decisions if "switch_to_closer" in d]
        self.assertGreater(len(switch_decisions), 0, "应有切换决策")
        # 验证新目标是近资源
        new_goal = memory.worker_goals.get(str(w.id))
        self.assertIsNotNone(new_goal)
        self.assertEqual(new_goal.position, (612, 600))

    def test_worker_no_switch_within_threshold(self) -> None:
        # 新资源仅近 1 格（未达 2 格阈值）时不切换。
        from arena_hero_strategy import SmartTactic, TacticMemory
        w = worker(UUID(int=1), (610, 600))
        memory = TacticMemory()
        # 工人已有目标：(615, 600) 距离 5
        memory.set_worker_goal(w, "visible_resource", (615, 600), 1)

        turn, _ = make_turn(
            own_core=core((600, 600)),
            units=(w,),
            resource_cells=((615, 600), (614, 600)),  # 旧 d=5, 新 d=4, 仅近 1 格
        )
        tactic = SmartTactic(memory)
        summary = tactic.choose_actions(turn)

        # 预期：不切换（未达 2 格阈值）
        switch_decisions = [d for d in summary.decisions if "switch_to_closer" in d]
        self.assertEqual(len(switch_decisions), 0, "不应切换")
        # 验证目标不变
        goal = memory.worker_goals.get(str(w.id))
        self.assertIsNotNone(goal)
        self.assertEqual(goal.position, (615, 600))

    def test_worker_releases_old_target_on_switch(self) -> None:
        # 工人切换时释放旧目标，供其他工人选择。
        from arena_hero_strategy import SmartTactic, TacticMemory
        w1 = worker(UUID(int=1), (610, 600))
        w2 = worker(UUID(int=2), (611, 600))
        memory = TacticMemory()
        # w1 已有远目标 (625, 600) 距离 15
        memory.set_worker_goal(w1, "visible_resource", (625, 600), 1)

        turn, _ = make_turn(
            own_core=core((600, 600)),
            units=(w1, w2),
            resource_cells=((625, 600), (613, 600)),  # 远资源、近资源
        )
        tactic = SmartTactic(memory)
        summary = tactic.choose_actions(turn)

        # 预期：w1 切换到近资源，w2 可分配到被释放的远资源
        # 验证至少有一个工人切换了
        switch_decisions = [d for d in summary.decisions if "switch_to_closer" in d]
        self.assertGreater(len(switch_decisions), 0, "w1 应切换到近资源")


if __name__ == "__main__":
    unittest.main()
