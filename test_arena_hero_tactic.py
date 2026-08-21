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
    ChampionBeacon,
    CommandSource,
    CoreState,
    CoreView,
    Direction,
    MoveAction,
    PlayerState,
    PlayerStatus,
    ResolutionEvent,
    ShootAction,
    SpawnAction,
    StartMoveAction,
    TerrainView,
    Turn,
    UnitType,
    UnitView,
)

from arena_hero_tactic import _write_agent_status
from arena_hero_strategy import (
    MovementPlanner,
    SmartTactic,
    TacticMemory,
    _destination,
    _distance,
    _enemy_can_see_cell,
    _enemy_watchers,
    _short_id,
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

    @staticmethod
    def _isolated_lightning_memory() -> TacticMemory:
        """Keep local-behaviour fixtures independent of Core migration.

        These tests intentionally place the Core at (600, 600) to exercise
        spawning and lane helpers.  Set a stable orbit radius so the Core's
        patrol/migration does not consume the Core action under test.
        """
        memory = TacticMemory()
        memory.core_orbit_radius = 550
        return memory

    def _box_core(self) -> CoreView:
        # Stable coordinate for the isolated lightning fixtures below.
        return core((600, 600))

    def test_cap10_forces_vanguard_not_ranger(self) -> None:
        # pop1 → 容量 10，资源 10：开局引导未造满 3 工人 → 造工人（游侠要 12 存不下）。
        turn, _ = make_turn(
            own_core=self._box_core(),
            units=(worker(WORKER_LOW, (601, 600)),),
            resources=10,
        )
        SmartTactic(self._isolated_lightning_memory()).choose_actions(turn)

        self.assertIsInstance(turn.plan.core_action, SpawnAction)
        self.assertEqual(turn.plan.core_action.unit_type, UnitType.WORKER)

    def test_second_worker_raises_cap_before_ranger(self) -> None:
        # pop2（工人+先锋），容量仍 10：开局引导仍要造工人抬容量。
        turn, _ = make_turn(
            own_core=self._box_core(),
            units=(
                worker(WORKER_LOW, (601, 600)),
                vanguard((602, 600)),
            ),
            resources=10,
        )
        SmartTactic(self._isolated_lightning_memory()).choose_actions(turn)

        self.assertIsInstance(turn.plan.core_action, SpawnAction)
        self.assertEqual(turn.plan.core_action.unit_type, UnitType.WORKER)

    def test_patrol_waypoint_on_square_perimeter(self) -> None:
        memory = TacticMemory()
        memory.core_orbit_radius = 450
        tactic = SmartTactic(memory)
        # 巡逻不需战斗护卫门槛（贫瘠区不能等攒够先锋+游侠才动）。
        # Core 置于轨道内侧（max-norm 半径 < r），仅 1 工人、0 战斗单位。
        turn, _ = make_turn(
            own_core=core((535, -201)),
            units=(worker(WORKER_LOW, (536, -201)),),
        )
        tactic.choose_actions(turn)
        waypoint = memory.lightning_patrol_waypoint
        self.assertIsNotNone(waypoint)
        radius = max(abs(waypoint[0]), abs(waypoint[1]))
        # 巡逻点应在半径 r=450 的方形周界角上。
        self.assertEqual(radius, 450)

    def test_core_threat_recalls_vanguard_instead_of_hunting(self) -> None:
        # 敌方先锋贴近 Core → 视为告急，先锋召回护核而非离开近轨。
        # （敌方 Core 猎杀链已废弃：先锋不再 claim/远征敌方 Core，只守近轨。）
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

        # 先锋本 tick 应有防御动作（sweep 贴脸敌先锋 / 朝 Core 回防 / 就地 wait），
        # 而不是朝远方敌方 Core (610,600) 前进远征。
        action = turn.plan.unit_actions.get(VANGUARD_ID)
        self.assertIsNotNone(action, "先锋应对贴 Core 威胁做出防御响应，不能零指令")

    def test_patrol_continues_past_noncombat_enemy_worker(self) -> None:
        # 敌方工人在 Core 8 格内（无攻击力）→ Core 继续巡逻，不停摆。
        turn, _ = make_turn(
            own_core=core((535, -201)),
            units=(worker(WORKER_LOW, (540, -201)),),
            enemies=(enemy_worker((536, -201)),),
        )
        memory = TacticMemory()
        memory.core_orbit_radius = 650
        SmartTactic(memory).choose_actions(turn)
        self.assertIsInstance(turn.plan.core_action, StartMoveAction)

    def test_combat_anchor_holds_against_near_vanguard(self) -> None:
        # 动态 R_commit 内的先锋不能再诱使 Core 启动四 Tick 迁移；迁移期间
        # 无法治疗/造兵/补盾，会直接破坏内层防线。
        turn, _ = make_turn(
            own_core=core((535, -201)),
            units=(worker(WORKER_LOW, (545, -180)),),
            enemies=(enemy_vanguard((541, -201)),),
        )
        summary = SmartTactic(TacticMemory()).choose_actions(turn)
        self.assertIsNone(turn.plan.core_action)
        self.assertTrue(any("core anchor_hold state=COMBAT_ANCHOR" in d for d in summary.decisions))

    def test_combat_anchor_holds_against_adjacent_vanguard(self) -> None:
        # 相邻先锋本 Tick 就能 sweep；Core 必须服务战斗，而不是逃跑进入 4 Tick 锁定。
        turn, _ = make_turn(
            own_core=core((535, -201)),
            units=(worker(WORKER_LOW, (540, -201)),),
            enemies=(enemy_vanguard((536, -201)),),
        )
        SmartTactic(TacticMemory()).choose_actions(turn)
        self.assertIsNone(turn.plan.core_action)

    def test_patrol_moves_toward_waypoint_not_origin(self) -> None:
        # Core 在 (535,-201)，r=650 时最近巡逻角 (650,-650)。应朝巡逻点（远离原点）
        # 走，而非被 beacon_progress 拉向原点（旧 bug 会漂向 LEFT/UP）。
        turn, _ = make_turn(
            own_core=core((535, -201)),
            units=(worker(WORKER_LOW, (540, -201)),),
        )
        memory = TacticMemory()
        memory.core_orbit_radius = 650
        SmartTactic(memory).choose_actions(turn)
        self.assertIsInstance(turn.plan.core_action, StartMoveAction)
        dest = _destination((535, -201), turn.plan.core_action.direction)
        self.assertLess(
            _distance(dest, (650, -650)),
            _distance((535, -201), (650, -650)),
        )

    # ---- 侦察改造:并排游侠探路 + 先锋 V 字纵深 + 集火 ----

    def test_ranger_step_does_not_use_astar(self) -> None:
        # 游侠绕圈走 Core 风格四邻打分(_lightning_step_toward),不走 A*。
        # 空旷地形朝目标角单调推进,产生 mid_orbit_patrol 决策(非 fallback)。
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
        # Core 离原点 1200；取消突破轨后所有游侠走中行星轨道单步,
        # 理由为 mid_orbit_patrol(四邻打分,非 A* fallback)。
        self.assertTrue(
            any("reason=mid_orbit_patrol" in d for d in decisions),
            f"expected mid_orbit_patrol decision, got {decisions}",
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
        # 连走 6 步,每步重建 planner 模拟游侠移动,断言不在两格间横跳。
        positions = [(650, -653)]
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

    def test_lightning_banks_when_slot0_vanguard_unaffordable(self) -> None:
        # 开局引导前 3 个造工人：pop1(1 免费工人)、资源 5 恰好够造工人(价 5)
        # → 造工人；买不起的是先锋/游侠,但引导期不造它们。这里验证资源不足
        # 造工人时才攒钱:把资源降到 4(买不起工人价 5)→ 不造,攒钱。
        turn, _ = make_turn(
            own_core=self._box_core(),
            units=(worker(WORKER_LOW, (601, 600)),),
            resources=4,
        )
        SmartTactic(TacticMemory()).choose_actions(turn)
        self.assertNotIsInstance(turn.plan.core_action, SpawnAction)

    def test_no_fixed_build_order_from_first_unit(self) -> None:
        # 老的"前 N 个固定阶梯"已移除：从第 1 个单位起，无队列无阈值时一律走
        # _lightning_ratio_spawn 按默认比例 1:1:3 趋近。空编队归一化计数全 0，
        # 平局按补兵优先级（游侠 > 工人 > 先锋）→ 第一个造游侠。
        from arena_hero_strategy import SmartTactic
        tactic = SmartTactic(TacticMemory())
        turn, _ = make_turn(own_core=core((600, 600)), units=())
        self.assertIs(
            tactic._lightning_ratio_spawn(turn, died={}),
            UnitType.RANGER,
            "空编队首兵应按比例 1:1:3 + 优先级补游侠，而非固定先锋",
        )
        # 再造一个：rk=1 后归一化 游侠1/1=1, 先锋0/1=0, 工人0/3=0 → 先锋/工人
        # 归一化同为 0，平局按优先级（工人 > 先锋）→ 工人。
        units = (ranger((610, 600), UUID(int=0xF100)),)
        turn2, _ = make_turn(own_core=core((600, 600)), units=units)
        self.assertIs(tactic._lightning_ratio_spawn(turn2, died={}), UnitType.WORKER)

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
        # FAR:落在动态 sensor 外沿、但未越过它的游侠 → 局部响应,不全撤。
        turn_far, _ = make_turn(
            own_core=core((600, 600)),
            units=(vanguard((601, 600)),),
            enemies=(enemy_ranger((625, 600)),),
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
        memory = self._isolated_lightning_memory()
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

    # ---- 轨道均匀分布软斥力(A: reason 白名单) ----

    def test_orbit_spread_detours_away_from_adjacent_friendly(self) -> None:
        # 软斥力只作用于 ORBIT_SPREAD_REASONS(纯巡逻)。两个游侠同层巡逻目标一致,
        # 后方的友军在候选格脚下 → 候选那格因斥力被罚,单位改走更开阔的方向,
        # 从而撑开扎堆。open-field(目标在 +x 大方向)时 target_distance 项几乎被
        # +x/-x 两个方向的 diffs 抹平,斥力足以扭转选择。
        from arena_hero_strategy import MovementPlanner
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        uid = str(UUID(int=0xB040))
        # 本单位的角点(最近角,半径 14 → (646,646));目标在 +x 远端。
        turn, _ = make_turn(
            own_core=core((600, 600)),
            units=(
                ranger((632, 600), UUID(int=0xB040)),
                ranger((630, 600), UUID(int=0xB041)),
            ),
        )
        decisions: list[str] = []
        planner = MovementPlanner(turn, memory, decisions)
        moved = tactic._lightning_step_toward(
            turn, planner, turn.rangers[0], (700, 600), "mid_orbit_patrol"
        )
        self.assertTrue(moved)
        move_line = next(d for d in decisions if "move" in d and f"ranger:{uid[:8]}" in d)
        self.assertIn("to=(633, 600)", move_line, move_line)

    def test_spread_does_not_apply_to_combat_reason(self) -> None:
        # 战斗移动(拦截/狙击)用非白名单 reason → 零斥力,单位仍继续扑向目标,
        # 不会被身边友军推开而各自为战。
        from arena_hero_strategy import MovementPlanner
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        uid = str(UUID(int=0xB042))
        turn, _ = make_turn(
            own_core=core((600, 600)),
            units=(
                ranger((632, 600), UUID(int=0xB042)),
                ranger((630, 600), UUID(int=0xB043)),
            ),
        )
        decisions: list[str] = []
        planner = MovementPlanner(turn, memory, decisions)
        moved = tactic._lightning_step_toward(
            turn, planner, turn.rangers[0], (606, 600), "vanguard_committed_intercept"
        )
        self.assertTrue(moved)
        # 无视身后友军,径直朝目标 -x 走。
        move_line = next(d for d in decisions if "move" in d and f"ranger:{uid[:8]}" in d)
        self.assertIn("to=(631, 600)", move_line, move_line)

    def test_spread_skipped_during_escape(self) -> None:
        # 逃生(escaping)模式完全跳过斥力——脱困优先级最高,可无视均匀分布。

        from arena_hero_strategy import MovementPlanner
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        uid = str(UUID(int=0xB044))
        memory.lightning_unit_escape_until[uid] = 200  # 已在逃生期
        # 口袋:上下两排墙 + 左端封底,开口朝右。逃生应向右出口袋,不被身后友军拦。
        walls = (
            (648, 600),
            (649, 599), (650, 599), (651, 599),
            (649, 601), (650, 601), (651, 601),
        )
        memory.known_obstacles = set(walls)
        turn, _ = make_turn(
            tick=100,
            own_core=core((600, 600)),
            units=(
                ranger((650, 600), UUID(int=0xB044)),
                ranger((649, 600), UUID(int=0xB045)),
            ),
        )
        decisions: list[str] = []
        planner = MovementPlanner(turn, memory, decisions)
        moved = tactic._lightning_step_toward(
            turn, planner, turn.rangers[0], (620, 600), "mid_orbit_patrol"
        )
        self.assertTrue(moved)
        # 即便身后友军在候选 -y/+y 附近,逃生仍向右走出口袋(开阔度优先)。
        move_line = next(d for d in decisions if "move" in d and ":escape" in d)
        self.assertIn("to=(651, 600)", move_line, move_line)

    def test_spread_does_not_affect_solo_unit(self) -> None:
        # 无其他友军 → count_observed 缓存为空、spread_friends 为空,移动与原来完全一致。
        from arena_hero_strategy import MovementPlanner
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        turn, _ = make_turn(
            own_core=core((600, 600)),
            units=(ranger((650, 600), UUID(int=0xB046)),),
        )
        turn.units = [turn.units[0]]  # 只有自己
        decisions: list[str] = []
        planner = MovementPlanner(turn, memory, decisions)
        tactic._lightning_step_toward(
            turn, planner, turn.rangers[0], (620, 600), "mid_orbit_patrol"
        )
        # 无其他友军 → spread_friends 为空,移动与原来完全一致:径直朝目标 +x 收缩。
        move_line = next(d for d in decisions if "move" in d)
        self.assertIn("to=(649, 600)", move_line)

    def test_core_patrol_waypoint_skips_obstructed_corner(self) -> None:
        # Core 巡逻角埋在乱石堆里 → 距角尚远时提前跳下一角。
        memory = TacticMemory()
        memory.core_orbit_radius = 600
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

    def test_no_emergency_worker_on_cap(self) -> None:
        # 删除 urgency_threshold 后:资源满仓(80%+)且 pop<100 不再因满仓而造工人。
        # 设置:pop=20 (容量 100), resources=80 (80%),20 人全工人(rk=0,wk=20)。
        # pop≥9 走 ratio:rk<3*wk → 按 3:1 应补游侠(而非旧的紧急工人)。
        from arena_hero_strategy import SmartTactic
        units = tuple(worker(UUID(int=i), (600 + i % 10, 600 + i // 10)) for i in range(20))
        turn, _ = make_turn(
            own_core=core((600, 600)),
            units=units,
            resources=80,  # 容量 max(10, 20*5)=100, 80/100=80%
        )
        SmartTactic(self._isolated_lightning_memory()).choose_actions(turn)
        # 预期:造游侠(3:1 趋近),不是因满仓造工人。
        self.assertIsInstance(turn.plan.core_action, SpawnAction)
        self.assertEqual(turn.plan.core_action.unit_type, UnitType.RANGER)

    def test_spawn_stops_at_absolute_cap(self) -> None:
        # pop=105 (ABSOLUTE_MAX_POPULATION) 时硬顶不再产兵,即使资源很多。
        from arena_hero_strategy import SmartTactic
        units = tuple(worker(UUID(int=i), (600 + i % 30, 600 + i // 30)) for i in range(105))
        turn, _ = make_turn(
            own_core=core((600, 600)),
            units=units,
            resources=600,
        )
        SmartTactic(TacticMemory()).choose_actions(turn)
        # 预期:不造兵(已达 ABSOLUTE_MAX_POPULATION=105)
        self.assertNotIsInstance(turn.plan.core_action, SpawnAction)

    def test_spawn_ratio_under_soft_cap_when_capacity_ok(self) -> None:
        # pop=10 (容量 50), resources 充足:无固定阶梯,直接走 ratio(1:1:3)。
        # 10 人全工人(rk=0,wk=10) → 游侠归一化 0/1=0 最低(平局按优先级游侠>工人>先锋)
        # → 补游侠。
        from arena_hero_strategy import SmartTactic
        units = tuple(worker(UUID(int=i), (600 + i, 600)) for i in range(10))
        turn, _ = make_turn(
            own_core=core((600, 600)),
            units=units,
            resources=100,  # 远超成本,确保买得起
        )
        SmartTactic(self._isolated_lightning_memory()).choose_actions(turn)
        self.assertIsInstance(turn.plan.core_action, SpawnAction)
        self.assertEqual(turn.plan.core_action.unit_type, UnitType.RANGER)

    def test_medical_ranger_spawn_respects_spawn_ratio_zero(self) -> None:
        # 回归:医疗空缺应急分支(medical_ranger)必须遵守 spawn_ratio=0(停造该兵种)。
        # 旧代码应急分支只查 _unit_capped,绕过 spawn_ratio → 玩家把游侠比例设 0 仍造游侠。
        # 场景:无固定阶梯,直接走 _lightning_ratio_spawn;含 1 名 hp1 游侠(医疗空缺)、
        # 无满血游侠可替补 → 应急分支想造游侠;spawn_ratio.ranger=0 时不得造游侠。
        from arena_hero_strategy import SmartTactic, TacticMemory
        wounded = ranger((610, 600), RANGER_ID, hp=1)
        # 9 名工人让 pop=10 → 走 _lightning_ratio_spawn(无固定阶梯)。
        workers = tuple(worker(UUID(int=0x1000 + i), (601 + i, 599)) for i in range(9))
        turn, _ = make_turn(
            own_core=core((600, 600)),
            units=(wounded,) + workers,
            enemies=(enemy_vanguard((612, 600)),),
            resources=500,  # 充足,确保买得起
        )
        # ratio 全 0(停造) → 医疗应急分支不得绕过 → 不造游侠(也不造其他兵)。
        mem_zero = TacticMemory()
        mem_zero.core_orbit_radius = 550
        mem_zero.spawn_ratio = {"ranger": 0, "vanguard": 0, "worker": 0}
        SmartTactic(mem_zero).choose_actions(turn)
        action = turn.plan.core_action
        self.assertNotIsInstance(
            action, SpawnAction,
            f"spawn_ratio 全 0 时不得造任何兵(含医疗应急游侠),实际={action!r}",
        )
        # 对照:放开游侠比例 → 医疗空缺应急分支应造游侠。
        mem_ok = TacticMemory()
        mem_ok.core_orbit_radius = 550
        mem_ok.spawn_ratio = {"ranger": 3, "vanguard": 1, "worker": 1}
        SmartTactic(mem_ok).choose_actions(turn)
        action_ok = turn.plan.core_action
        self.assertIsInstance(action_ok, SpawnAction)
        self.assertEqual(
            action_ok.unit_type, UnitType.RANGER,
            f"放开游侠比例后医疗应急应造游侠,实际={action_ok!r}",
        )

    def test_core_hold_suppresses_evade_during_invasion(self) -> None:
        # 回归:core_hold=true 时,即使有敌方入侵(T4 近圈威胁),Core 必须停在原地,
        # 不得因 MOBILE_EVADE/巡逻本能往入侵相反方向逃跑。
        # 旧代码 core_hold 检查在 else 分支里,造兵/修盾的 tick 会 fall through 到
        # 迁移逻辑 → Core 面对入侵时逃跑。修复后 core_hold 在造兵/修盾之后、迁移之前拦截。
        from arena_hero import StartMoveAction
        from arena_hero_strategy import SmartTactic, TacticMemory
        # 敌先锋贴脸(距 Core 1 格)→ 近圈威胁,plan.anchor 非 MOBILE_EVADE 即 COMBAT,
        # 没有造兵/修盾余地(无资源)→ 必须命中 core_hold 分支,不动。
        turn, _ = make_turn(
            own_core=core((600, 600)),
            units=(ranger((610, 600), RANGER_ID),),
            enemies=(enemy_vanguard((601, 600)),),
            resources=0,
        )
        mem = TacticMemory()
        mem.core_orbit_radius = 550
        mem.core_hold = True
        SmartTactic(mem).choose_actions(turn)
        action = turn.plan.core_action
        # 驻扎时 Core 不得移动(逃跑)。无资源不造兵/修盾,动作应为空或非 StartMove。
        self.assertNotIsInstance(
            action, StartMoveAction,
            f"core_hold=true 时 Core 不得移动逃避入侵,实际={action!r}",
        )

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

    def test_electronic_orbit_distribution_11_rangers(self) -> None:
        """电子排布：11 个游侠，层容量=2n 循环队列填充。"""
        memory = TacticMemory()
        tactic = SmartTactic(memory)

        # 11 个游侠，gap=5，inner=10
        result = tactic._lightning_calculate_outer_first_orbits(
            unit_count=11, gap=5, inner_radius=10
        )

        # 总数必须恰好 11
        self.assertEqual(sum(c for _, c in result), 11)
        # 半径从 inner=10 起按 gap=5 递增，无空洞层
        radii = [r for r, _ in result]
        self.assertEqual(radii, [10, 15, 20, 25])
        # 电子排布层容量：层1=2, 层2=4, 层3=4, 层4=1
        self.assertEqual(dict(result), {10: 2, 15: 4, 20: 4, 25: 1})

    def test_electronic_orbit_distribution_grows_outer_layers(self) -> None:
        """25 个游侠：外层(周长大)承载更多单位,体现分层防御。"""
        memory = TacticMemory()
        tactic = SmartTactic(memory)

        result = tactic._lightning_calculate_outer_first_orbits(
            unit_count=25, gap=5, inner_radius=10
        )

        self.assertEqual(sum(c for _, c in result), 25)
        # 半径连续无洞
        radii = [r for r, _ in result]
        self.assertEqual(radii, [10, 15, 20, 25, 30, 35])
        # 外层(35)单位数 >= 内层(10)
        counts = dict(result)
        self.assertGreaterEqual(counts[35], counts[10])

    def test_orbit_distribution_counts_all_units_at_scale(self) -> None:
        """50 个单位：总数必须全部落位,半径连续。"""
        memory = TacticMemory()
        tactic = SmartTactic(memory)

        result = tactic._lightning_calculate_outer_first_orbits(
            unit_count=50, gap=5, inner_radius=10
        )

        # 不丢单位
        self.assertEqual(sum(c for _, c in result), 50)
        # 半径连续递增
        radii = [r for r, _ in result]
        self.assertEqual(radii, list(range(10, 10 + 5 * len(radii), 5)))

    def test_orbit_phase_offset_distributes_units(self) -> None:
        """验证同半径多单位通过 phase_offset 错开到不同角。"""
        memory = self._isolated_lightning_memory()
        tactic = SmartTactic(memory)

        # 构造场景：r=10 有 3 个游侠
        r1 = ranger((605, 605), UUID(int=0xE001))
        r2 = ranger((615, 605), UUID(int=0xE002))
        r3 = ranger((625, 605), UUID(int=0xE003))

        turn, _ = make_turn(
            own_core=core((600, 600)),
            units=(r1, r2, r3),
        )

        # 手动设置分配：3 个都在 r=10
        memory.lightning_orbit_lanes[UnitType.RANGER.value] = {
            str(r1.id): (10, 0),
            str(r2.id): (10, 1),
            str(r3.id): (10, 2),
        }

        # 获取各自的目标点
        target1 = tactic._lightning_orbit_waypoint(turn, r1, UnitType.RANGER)
        target2 = tactic._lightning_orbit_waypoint(turn, r2, UnitType.RANGER)
        target3 = tactic._lightning_orbit_waypoint(turn, r3, UnitType.RANGER)

        # 验证目标点不同（phase_offset 生效）
        self.assertIsNotNone(target1)
        self.assertIsNotNone(target2)
        self.assertIsNotNone(target3)

        targets = {target1, target2, target3}
        # 3 个单位应分布在至少 2 个不同的角（phase_offset = 0, 1, 2 → 角 0, 1, 2）
        self.assertGreaterEqual(len(targets), 2, "同半径单位应分散到不同角")


class SharedOrbitTests(unittest.TestCase):
    """游侠+工人共用中轨(单一有序队列):游侠占内层、工人接外层、新游侠挤出工人。"""

    @staticmethod
    def _rk_wk_turn(num_rangers: int, num_workers: int, core_pos=(600, 600)):
        """构造 num_rangers 游侠 + num_workers 工人的 turn(UUID 按 0xD0 段递增)。"""
        units = []
        for i in range(num_rangers):
            units.append(
                ranger((core_pos[0] + 10 + i, core_pos[1]), UUID(int=0xD100 + i))
            )
        for j in range(num_workers):
            units.append(
                worker(UUID(int=0xD200 + j), (core_pos[0] - 10 - j, core_pos[1]))
            )
        turn, _ = make_turn(own_core=core(core_pos), units=tuple(units))
        return turn

    def test_shared_orbit_rangers_inner_workers_outer(self) -> None:
        # 3 游侠 + 2 工人:游侠序号 [0,3),工人序号 [3,5);位置半径非递减,
        # 故工人整体不比游侠更靠内(min 工人 radius ≥ min 游侠 radius)。
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        turn = self._rk_wk_turn(num_rangers=3, num_workers=2)
        tactic._lightning_assign_shared_middle_lanes(turn)

        seq = memory.lightning_shared_orbit_seq
        ranger_lanes = memory.lightning_orbit_lanes[UnitType.RANGER.value]
        worker_lanes = memory.lightning_orbit_lanes[UnitType.WORKER.value]

        # 游侠 3 个、工人 2 个,序号互不重叠。
        self.assertEqual(len(ranger_lanes), 3)
        self.assertEqual(len(worker_lanes), 2)
        ranger_seqs = {seq[uid] for uid in ranger_lanes}
        worker_seqs = {seq[uid] for uid in worker_lanes}
        self.assertEqual(ranger_seqs, {0, 1, 2})
        self.assertEqual(worker_seqs, {3, 4})

        # 不变量:工人 radius ≥ 游侠最小 radius(工人不钻到游侠内侧)。
        min_ranger_r = min(r for r, _ in ranger_lanes.values())
        for r, _ in worker_lanes.values():
            self.assertGreaterEqual(
                r, min_ranger_r, f"工人 radius {r} 比最内游侠 {min_ranger_r} 还内"
            )

    def test_new_ranger_pushes_worker_outward(self) -> None:
        # rk=3 wk=2 时工人序号 3,4;造第 4 游侠后 rk=4 wk=2 → 游侠段扩到 [0,4),
        # 原序号 3 的工人被推到序号 4,radius 增大(外推)。
        memory = TacticMemory()
        tactic = SmartTactic(memory)

        # 第一阶段:3 游侠 2 工人。记录工人的 radius。
        turn1 = self._rk_wk_turn(num_rangers=3, num_workers=2)
        tactic._lightning_assign_shared_middle_lanes(turn1)
        worker_lanes_1 = dict(memory.lightning_orbit_lanes[UnitType.WORKER.value])
        self.assertEqual(len(worker_lanes_1), 2)

        # 第二阶段:4 游侠 2 工人(同样的两个工人 UUID,新增一个游侠)。
        turn2 = self._rk_wk_turn(num_rangers=4, num_workers=2)
        tactic._lightning_assign_shared_middle_lanes(turn2)
        worker_lanes_2 = dict(memory.lightning_orbit_lanes[UnitType.WORKER.value])

        # 同一批工人 UUID,造新游侠后 radius 应不减小(被往外挤或持平)。
        for uid in worker_lanes_1:
            self.assertIn(uid, worker_lanes_2)
            self.assertGreaterEqual(
                worker_lanes_2[uid][0],
                worker_lanes_1[uid][0],
                f"工人 {uid} 被挤出后 radius 应不减小: "
                f"{worker_lanes_1[uid][0]} → {worker_lanes_2[uid][0]}",
            )
        # 至少一个工人的 radius 严格增大(外推效果可见)。
        pushed = any(
            worker_lanes_2[uid][0] > worker_lanes_1[uid][0]
            for uid in worker_lanes_1
        )
        self.assertTrue(pushed, "新游侠应把至少一个工人往外挤")

    def test_stable_when_counts_unchanged(self) -> None:
        # 连续两次同样人数(rk=3 wk=2)→ lanes 完全一致(不抖动)。
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        turn1 = self._rk_wk_turn(num_rangers=3, num_workers=2)
        tactic._lightning_assign_shared_middle_lanes(turn1)
        lanes_1 = {
            role: dict(v) for role, v in memory.lightning_orbit_lanes.items()
        }
        seq_1 = dict(memory.lightning_shared_orbit_seq)

        # 第二次:同样单位,应复用 cached,不重算。
        turn2 = self._rk_wk_turn(num_rangers=3, num_workers=2)
        tactic._lightning_assign_shared_middle_lanes(turn2)
        lanes_2 = {
            role: dict(v) for role, v in memory.lightning_orbit_lanes.items()
        }
        seq_2 = dict(memory.lightning_shared_orbit_seq)

        self.assertEqual(lanes_1, lanes_2, "人数不变时 lanes 不应抖动")
        self.assertEqual(seq_1, seq_2, "人数不变时 seq 不应抖动")

    def test_mixed_layer_phase_offset_uses_combined_count(self) -> None:
        # §7 关键:同一半径混合游侠+工人时,phase_offset 按"合并后该层单位总数"算。
        # 构造游侠+工人在同一半径层,验证 _lightning_orbit_waypoint 读的 units_at_radius
        # 是合并值(游侠+工人),而非单 role 值。
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        # 2 游侠 + 2 工人:total=4 → 电子排布 total=4 的分布。
        turn = self._rk_wk_turn(num_rangers=2, num_workers=2)
        merged = tactic._lightning_assign_shared_middle_lanes(turn)
        # 找出混合层(同半径同时有游侠和工人)。
        ranger_radii = {
            r for uid, (r, _) in merged.items()
            if uid in {str(u.id) for u in turn.rangers}
        }
        worker_radii = {
            r for uid, (r, _) in merged.items()
            if uid in {str(u.id) for u in turn.workers}
        }
        mixed = ranger_radii & worker_radii
        if mixed:
            mixed_r = next(iter(mixed))
            # 该层合并单位数 = 游侠数 + 工人数(同层)。
            combined = sum(1 for (r, _) in merged.values() if r == mixed_r)
            ranger_only = sum(
                1 for uid, (r, _) in merged.items()
                if r == mixed_r
                and uid in {str(u.id) for u in turn.rangers}
            )
            self.assertGreater(
                combined, ranger_only,
                "混合层的 units_at_radius 必须是游侠+工人合计,而非单 role",
            )

    def test_load_roundtrip_shared_seq(self) -> None:
        # save→load 后 lightning_shared_orbit_seq 和 lanes 完整保留。
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        turn = self._rk_wk_turn(num_rangers=3, num_workers=2)
        tactic._lightning_assign_shared_middle_lanes(turn)
        seq_before = dict(memory.lightning_shared_orbit_seq)
        lanes_before = {
            role: dict(v) for role, v in memory.lightning_orbit_lanes.items()
        }
        self.assertGreater(len(seq_before), 0)

        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "memory.json"
            memory.save(path)
            restored = TacticMemory.load(path)

        # seq 完整恢复(uid→int)。
        self.assertEqual(restored.lightning_shared_orbit_seq, seq_before)
        # lanes 完整恢复,且值是 (radius, group_idx) 元组。
        for role in (UnitType.RANGER.value, UnitType.WORKER.value):
            self.assertEqual(
                restored.lightning_orbit_lanes[role], lanes_before[role]
            )
            for value in restored.lightning_orbit_lanes[role].values():
                self.assertIsInstance(value, tuple)
                self.assertEqual(len(value), 2)


class SpawnRatioTests(unittest.TestCase):
    """统一产兵：1:1:3(游侠:先锋:工人)比例趋近 + 阈值补兵 + 优先级。"""

    @staticmethod
    def _turn_with(rk: int, wk: int, vg: int = 1):
        units = []
        for i in range(vg):
            units.append(vanguard((700 + i, 700), UUID(int=0xF000 + i)))
        for i in range(rk):
            units.append(ranger((610 + i, 600), UUID(int=0xF100 + i)))
        for i in range(wk):
            units.append(worker(UUID(int=0xF200 + i), (590 - i, 600)))
        turn, _ = make_turn(own_core=core((600, 600)), units=tuple(units))
        return turn

    def test_spawn_1to1to3_ratio(self) -> None:
        # 默认比例 spawn_ratio = 游侠1 : 先锋1 : 工人3。从空编队连续纯增长
        # (无阵亡、无阈值),断言最终 wk ≈ 3×rk ≈ 3×vg(各 ±1 兵容差)。
        memory = TacticMemory()
        memory.unit_caps = {"ranger": 0, "vanguard": 0, "worker": 0}  # 关掉默认工人上限 20,测纯比例趋近
        tactic = SmartTactic(memory)
        rk, vg, wk = 0, 0, 0
        for _ in range(80):
            turn = self._turn_with(rk, wk, vg=vg)
            pick = tactic._lightning_ratio_spawn(turn, died={})
            self.assertIsNotNone(pick)
            if pick is UnitType.RANGER:
                rk += 1
            elif pick is UnitType.VANGUARD:
                vg += 1
            else:
                wk += 1
        # 工人数 ≈ 3×游侠数 ≈ 3×先锋数(允许 ±1 容差)。
        self.assertAlmostEqual(wk / max(1, rk), 3.0, delta=1.5,
                               msg=f"rk={rk} vg={vg} wk={wk} 工人:游侠未趋近 3:1")
        self.assertAlmostEqual(wk / max(1, vg), 3.0, delta=1.5,
                               msg=f"rk={rk} vg={vg} wk={wk} 工人:先锋未趋近 3:1")

    def test_spawn_replaces_dead_type(self) -> None:
        # 新逻辑不再"死什么补什么":阵亡只通过拉低当前数量间接触发比例趋近/
        # 阈值补兵。rk=6 wk=2 vg=1(比例 1:1:3 归一化 rk=6,wk≈0.67,vg=1)
        # → 工人归一化最低 → 补工人(无论死的是游侠还是先锋)。
        memory = TacticMemory()
        tactic = SmartTactic(memory)

        turn = self._turn_with(rk=6, wk=2, vg=1)
        pick = tactic._lightning_ratio_spawn(
            turn, died={"dead-rk": UnitType.RANGER.name}
        )
        self.assertIs(pick, UnitType.WORKER, "应按归一化最低补工人,而非死什么补什么")

        # 阈值驱动主动补兵:设游侠阈值=10(当前 rk=6 < 10)→ 应补游侠。
        memory.replenish_threshold = {"ranger": 10, "vanguard": 0, "worker": 0}
        turn = self._turn_with(rk=6, wk=2, vg=1)
        pick = tactic._lightning_ratio_spawn(
            turn, died={"dead-wk": UnitType.WORKER.name}
        )
        self.assertIs(pick, UnitType.RANGER, "低于阈值时按优先级主动补游侠")

    def test_spawn_keeps_vanguard_when_zero(self) -> None:
        # 先锋归一化最低(0/1=0)时补先锋。rk=1 wk=3 vg=0(比例 1:1:3):
        # 归一化 rk=1, wk=1, vg=0 → 先锋最低 → 补先锋。
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        turn = self._turn_with(rk=1, wk=3, vg=0)
        pick = tactic._lightning_ratio_spawn(
            turn, died={"dead-vg": UnitType.VANGUARD.name}
        )
        self.assertIs(pick, UnitType.VANGUARD)

    def test_select_spawn_survives_double_call_and_replaces_dead(self) -> None:
        # 集成:observe 算出本 tick 阵亡后,_select_spawn 被调多次(预检 + 决策)
        # 都应读到同一 died,pop≥9 时补阵亡的同种兵。
        from arena_hero_strategy import SmartTactic
        memory = TacticMemory()
        tactic = SmartTactic(memory)

        # 第 1 个 tick:rk=7 wk=2 vg=1(pop=10,slot9≥8 进入 ratio 逻辑)。
        rangers_t1 = [
            ranger((610 + i, 600), UUID(int=0xF100 + i)) for i in range(7)
        ]
        workers_t1 = [worker(UUID(int=0xF200 + i), (590 - i, 600)) for i in range(2)]
        vg_t1 = vanguard((700, 700), UUID(int=0xF000))
        turn1, _ = make_turn(
            own_core=core((600, 600)),
            units=tuple([vg_t1] + rangers_t1 + workers_t1),
            resources=1000,
        )
        memory.observe(turn1)
        self.assertEqual(memory.lightning_recent_deaths, {})  # 首 tick 无阵亡

        # 第 2 个 tick:一个游侠阵亡(rk=6 wk=2,pop=9 仍 ≥9),observe 记 recent_deaths。
        rangers_t2 = rangers_t1[1:]  # 第一个游侠死了
        turn2, _ = make_turn(
            own_core=core((600, 600)),
            units=tuple([vg_t1] + rangers_t2 + workers_t1),
            resources=1000,
        )
        memory.observe(turn2)
        died_uids = set(memory.lightning_recent_deaths.keys())
        self.assertEqual(len(died_uids), 1)
        self.assertEqual(
            memory.lightning_recent_deaths[next(iter(died_uids))],
            UnitType.RANGER.name,
        )

        # 模拟产兵预检和决策各调用一次:两次都应读同一个 died。
        pick1 = tactic._select_spawn_with_source(turn2, 1000)[0]
        pick2 = tactic._select_spawn_with_source(turn2, 1000)[0]
        # 新补兵逻辑不再"死什么补什么":rk=5 wk=2 vg=1 比例 1:1:3 归一化
        # rk=5, wk≈0.67, vg=1 → 工人最低 → 补工人;两次调用应返回同一选择
        # (阵亡信息不被消费)。
        self.assertIs(pick1, pick2, "两次调用应返回同一选择(died 不被消费)")


class OrbitalRepulsionTests(unittest.TestCase):
    """动态轨道排斥防御：测试边界、火控、医疗、漏斗和锚定的协同。"""

    @staticmethod
    def _squad(rangers: int, workers: int) -> tuple[UnitView, ...]:
        units: list[UnitView] = []
        for index in range(rangers):
            units.append(ranger((600 + index, 600), UUID(int=0xA000 + index)))
        for index in range(workers):
            units.append(worker(UUID(int=0xB000 + index), (630 + index, 600)))
        return tuple(units)

    def test_dynamic_threat_geometry_grows_with_three_to_one_lanes(self) -> None:
        # 3:1 编制增长会扩展最终 shared lane，因而外游侠/传感器边界随之扩展，
        # 而不是继续复用一组 6/20/40 防线数字。
        tactic = SmartTactic(TacticMemory())
        small, _ = make_turn(own_core=core((600, 600)), units=self._squad(3, 1))
        large, _ = make_turn(own_core=core((600, 600)), units=self._squad(9, 3))
        small_geometry = tactic._lightning_orbit_geometry(small)
        large_geometry = tactic._lightning_orbit_geometry(large)
        self.assertGreater(large_geometry.r_ranger_outer, small_geometry.r_ranger_outer)
        self.assertGreater(large_geometry.r_sensor_outer, small_geometry.r_sensor_outer)
        self.assertEqual(large_geometry.r_screen, large_geometry.r_ranger_inner)
        self.assertLess(large_geometry.r_commit, large_geometry.r_screen)

    def test_sparse_outer_contact_gets_same_sector_ranger_eta_support(self) -> None:
        turn, _ = make_turn(
            own_core=core((600, 600)),
            units=(
                ranger((610, 600)),
                ranger((590, 600), RANGER_TWO_ID),
                worker(WORKER_LOW, (620, 600)),
            ),
            enemies=(enemy_vanguard((625, 600)),),
        )
        summary = SmartTactic(TacticMemory()).choose_actions(turn)
        self.assertIsInstance(turn.plan.unit_actions[RANGER_ID], MoveAction)
        self.assertEqual(turn.plan.unit_actions[RANGER_ID].direction, Direction.RIGHT)
        self.assertTrue(any("ranger_eta_support" in line for line in summary.decisions))

    def test_one_hp_ranger_medivac_gets_health_ranger_relief(self) -> None:
        tactic = SmartTactic(TacticMemory())
        turn, _ = make_turn(
            own_core=core((600, 600)),
            units=(ranger((610, 600), hp=1), ranger((620, 600), RANGER_TWO_ID)),
        )
        summary = tactic.choose_actions(turn)
        plan = tactic._lightning_plan
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(len(plan.vacancies), 1)
        self.assertEqual(plan.vacancies[0].ranger_id, RANGER_ID)
        self.assertEqual(plan.vacancies[0].t_medical_gap, 21)
        self.assertEqual(len(plan.reliefs), 1)
        self.assertEqual(plan.reliefs[0].ranger_id, RANGER_TWO_ID)
        self.assertEqual(turn.plan.unit_actions[RANGER_ID].direction, Direction.LEFT)
        self.assertEqual(turn.plan.unit_actions[RANGER_TWO_ID].direction, Direction.LEFT)
        self.assertTrue(any("MEDIVAC" in line for line in summary.decisions))
        self.assertTrue(any("ranger_relief" in line for line in summary.decisions))

    def test_shot_ledger_stops_overkill_on_stationary_target(self) -> None:
        turn, _ = make_turn(
            own_core=core((600, 600)),
            units=(ranger((600, 600)), ranger((600, 602), RANGER_TWO_ID)),
            enemies=(enemy_vanguard((602, 600), hp=1),),
        )
        SmartTactic(TacticMemory()).choose_actions(turn)
        shots = [action for action in turn.plan.unit_actions.values() if isinstance(action, ShootAction)]
        self.assertEqual(len(shots), 1)
        self.assertEqual(shots[0].target_id, UUID(int=0x8002))

    def test_t3_legal_shot_beats_ranger_movement(self) -> None:
        turn, _ = make_turn(
            own_core=core((600, 600)),
            units=(ranger((606, 600)),),
            enemies=(enemy_vanguard((609, 600)),),
        )
        SmartTactic(TacticMemory()).choose_actions(turn)
        self.assertIsInstance(turn.plan.unit_actions[RANGER_ID], ShootAction)

    def test_committed_vanguard_intercepts_without_moving_toward_core(self) -> None:
        tactic = SmartTactic(TacticMemory())
        turn, _ = make_turn(
            own_core=core((600, 600)),
            units=(vanguard((604, 600)),),
            enemies=(enemy_vanguard((608, 600)),),
        )
        tactic.choose_actions(turn)
        plan = tactic._lightning_plan
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertIn(VANGUARD_ID, plan.committed_vanguards)
        action = turn.plan.unit_actions[VANGUARD_ID]
        self.assertIsInstance(action, MoveAction)
        self.assertEqual(action.direction, Direction.RIGHT)

    def test_worker_funnel_uses_covered_gate_and_blocks_other_route(self) -> None:
        tactic = SmartTactic(TacticMemory())
        turn, _ = make_turn(
            own_core=core((600, 600)),
            units=(ranger((605, 602)), worker(WORKER_LOW, (608, 600))),
            enemies=(enemy_vanguard((608, 602)),),
        )
        tactic.choose_actions(turn)
        plan = tactic._lightning_plan
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.funnel.gate_cell, (607, 602))
        self.assertEqual(plan.funnel.block_cells, ((608, 601),))
        self.assertEqual(plan.funnel.assignments, ((WORKER_LOW, (608, 601)),))
        # 该工人同时满足旧漏斗阻挡条件，但已进入敌先锋战斗接触；
        # 新规则逃跑优先，不能为了堵门继续向先锋火力线靠近。
        self.assertEqual(turn.plan.unit_actions[WORKER_LOW].direction, Direction.LEFT)

    def test_medical_and_combat_core_anchor_states(self) -> None:
        medical = SmartTactic(TacticMemory())
        medical_turn, _ = make_turn(
            own_core=core((600, 600)), units=(ranger((604, 600), hp=1),)
        )
        medical.choose_actions(medical_turn)
        self.assertEqual(medical._lightning_plan.anchor.value, "MEDICAL_ANCHOR")  # type: ignore[union-attr]
        self.assertIsNone(medical_turn.plan.core_action)

        combat = SmartTactic(TacticMemory())
        combat_turn, _ = make_turn(
            own_core=core((600, 600)),
            units=(ranger((605, 602)),),
            enemies=(enemy_vanguard((608, 602)),),
        )
        combat.choose_actions(combat_turn)
        self.assertEqual(combat._lightning_plan.anchor.value, "COMBAT_ANCHOR")  # type: ignore[union-attr]
        self.assertIsNone(combat_turn.plan.core_action)

    def test_response_units_reset_to_orbit_after_contact_is_driven_off(self) -> None:
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        pressured, _ = make_turn(
            tick=8,
            own_core=core((600, 600)),
            units=(ranger((606, 600)),),
            enemies=(enemy_vanguard((609, 600)),),
        )
        tactic.choose_actions(pressured)
        clear, _ = make_turn(
            tick=9,
            own_core=core((600, 600)),
            units=(ranger((606, 600)),),
        )
        summary = tactic.choose_actions(clear)
        self.assertTrue(any("mid_orbit_patrol" in line for line in summary.decisions))
        self.assertFalse(any("ranger_eta_support" in line or "ranger_funnel_cover" in line for line in summary.decisions))


class StandoffRelayAndBlindSpotTests(unittest.TestCase):
    """机制二：对峙僵局换血 + 敌方（游侠/先锋）视野盲区火力位。"""

    def test_enemy_watchers_only_ranger_and_vanguard(self) -> None:
        # 敌方工人与 Core 不参与盲区计算（用户拍板）。
        turn, _ = make_turn(
            own_core=core((560, 600)),
            units=(ranger((600, 600)),),
            enemies=(
                enemy_ranger((620, 600)),
                enemy_vanguard((618, 602)),
                enemy_worker((610, 604)),
                enemy_core((640, 600)),
            ),
        )
        watchers = _enemy_watchers(turn)
        positions = {origin for origin, _ in watchers}
        self.assertEqual(positions, {(620, 600), (618, 602)})

    def test_blind_cell_behind_obstacle(self) -> None:
        # 石墙挡住敌游侠到 (600,597) 的对角视线 → 该格是盲区火力位。
        # (600,603) 曼哈顿 6 > R5 同为盲格；(601,602) 曼哈顿 4 且视线无阻 → 可见。
        turn, _ = make_turn(
            own_core=core((560, 600)),
            units=(ranger((570, 590)),),
            enemies=(enemy_ranger((603, 600)),),
            obstacle_cells=((602, 598),),
        )
        watchers = _enemy_watchers(turn)
        self.assertFalse(_enemy_can_see_cell(watchers, (600, 597), {(602, 598)}))
        self.assertTrue(_enemy_can_see_cell(watchers, (601, 602), {(602, 598)}))

    def test_standoff_detected_for_stationary_enemy_ranger(self) -> None:
        # 敌游侠近 4 帧原地（等我方先动）且我方游侠在 4 格内 → 判对峙。
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        for tick in range(4):
            turn, _ = make_turn(
                tick=tick + 1,
                own_core=core((560, 600)),
                units=(ranger((600, 600)),),
                enemies=(enemy_ranger((603, 600)),),
            )
            tactic.memory.observe(turn)
        standoff = tactic._detect_strategic_standoff(turn)
        self.assertIsNotNone(standoff)
        assert standoff is not None
        self.assertEqual(standoff.enemy.position, (603, 600))

    def test_standoff_relay_picks_blind_diagonal_cell(self) -> None:
        # 对峙时换血位优先选盲区对角远位（石墙背后），而非明处对角。
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        obstacles = {(602, 598)}
        turn, _ = make_turn(
            tick=4,
            own_core=core((560, 600)),
            units=(ranger((570, 590), UUID(int=0xB030)),),
            enemies=(enemy_ranger((603, 600)),),
            obstacle_cells=obstacles,
        )
        planner = MovementPlanner(turn, memory, [])
        standoff = enemy_ranger((603, 600))
        cell = tactic._standoff_relay_cell(turn, standoff, planner)
        self.assertIsNotNone(cell)
        self.assertEqual(cell, (600, 597))

    def test_standoff_relay_falls_back_without_blind_cell(self) -> None:
        # 无障碍 → 无盲区 → 回退普通距离 3 对角格（换血仍成立）。
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        turn, _ = make_turn(
            tick=4,
            own_core=core((560, 600)),
            units=(ranger((570, 590), UUID(int=0xB030)),),
            enemies=(enemy_ranger((603, 600)),),
        )
        planner = MovementPlanner(turn, memory, [])
        standoff = enemy_ranger((603, 600))
        cell = tactic._standoff_relay_cell(turn, standoff, planner)
        self.assertIsNotNone(cell)
        self.assertEqual(cell, (600, 597))

    def test_choose_actions_assigns_relay_ranger_to_standoff(self) -> None:
        # 端到端：对峙僵局 + 未参战游侠 → 它被指派走向盲区换血位，而不是巡逻。
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        # 先用 4 帧建立敌游侠"原地僵持"的轨迹。
        for tick in range(4):
            warm, _ = make_turn(
                tick=tick + 1,
                own_core=core((560, 600)),
                units=(ranger((600, 600)),),
                enemies=(enemy_ranger((603, 600)),),
                obstacle_cells=((602, 598),),
            )
            tactic.memory.observe(warm)
        relay_view = ranger((570, 590), UUID(int=0xB030))
        turn, _ = make_turn(
            tick=5,
            own_core=core((560, 600)),
            units=(ranger((600, 600)), relay_view),
            enemies=(enemy_ranger((603, 600)),),
            obstacle_cells=((602, 598),),
        )
        summary = tactic.choose_actions(turn)
        self.assertTrue(
            any("standoff_relay_advance" in line for line in summary.decisions),
            summary.decisions,
        )
        # 换血游侠的动作是移动而非射击。
        relay_action = turn.plan.unit_actions.get(relay_view.id)
        self.assertIsNotNone(relay_action)
        self.assertIsInstance(relay_action, MoveAction)

    def test_ranger_on_orbit_anchor_always_issues_action(self) -> None:
        # 游侠零指令回归(607c149 引入):当 _lightning_orbit_waypoint 返回 None
        # (已到轨道点 / lane 缺失),旧兜底 `goal = ... or ranger.position` 让
        # position!=goal 为假,既不 move 也不 wait → 游侠本 tick 完全零指令。
        # 修复后到点应显式 wait 或 move 到下一轨道点,总之必有动作。
        from arena_hero import MoveAction, WaitAction
        r_unit = ranger((610, 610), UUID(int=0xB040))
        turn, _ = make_turn(own_core=core((600, 600)), units=(r_unit,))
        SmartTactic(TacticMemory()).choose_actions(turn)
        action = turn.plan.unit_actions.get(r_unit.id)
        self.assertIsNotNone(
            action,
            "游侠到轨道点不能零指令:应有 move(推进下一点位)或 wait(占位)",
        )
        self.assertIsInstance(action, (MoveAction, WaitAction))

    def test_ranger_emits_wait_not_silent_when_orbit_waypoint_none(self) -> None:
        # 游侠零指令回归核心:模拟 _lightning_orbit_waypoint 返回 None(已到点/lane
        # 缺失/core 为 None 的边界),修复前会让游侠本 tick 完全零指令(既不 move
        # 也不 wait);修复后必须显式 wait 占位,不能静默跳过。
        from arena_hero import WaitAction
        r_unit = ranger((610, 610), UUID(int=0xB040))
        turn, _ = make_turn(own_core=core((600, 600)), units=(r_unit,))
        tactic = SmartTactic(TacticMemory())
        # 强制轨道点返回 None,精准复现 bug 触发条件。
        tactic._lightning_orbit_waypoint = lambda *args, **kwargs: None
        tactic.choose_actions(turn)
        action = turn.plan.unit_actions.get(r_unit.id)
        self.assertIsInstance(
            action, WaitAction,
            f"_lightning_orbit_waypoint 返回 None 时游侠应 wait 占位,不能零指令;实际={action}",
        )


class CombatContactAndAssaultTests(unittest.TestCase):
    """战斗接触回归：工人先逃，游侠四人组主动覆盖火力。"""

    def test_loaded_worker_flees_before_returning_to_core(self) -> None:
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        turn, _ = make_turn(
            own_core=core((600, 600)),
            units=(worker(WORKER_LOW, (610, 600), cargo=1),),
            enemies=(enemy_vanguard((612, 600)),),
        )
        summary = tactic.choose_actions(turn)
        action = turn.plan.unit_actions.get(WORKER_LOW)
        self.assertIsInstance(action, MoveAction)
        self.assertTrue(
            any("combat_flee" in decision for decision in summary.decisions),
            summary.decisions,
        )
        self.assertNotIn("worker:deposit", " ".join(summary.decisions))

    def test_enemy_worker_does_not_trigger_combat_flee(self) -> None:
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        turn, _ = make_turn(
            own_core=core((600, 600)),
            units=(worker(WORKER_LOW, (610, 600)),),
            enemies=(enemy_worker((612, 600)),),
        )
        summary = tactic.choose_actions(turn)
        self.assertFalse(
            any("combat_flee" in decision for decision in summary.decisions),
            summary.decisions,
        )

    def test_four_rangers_split_preaim_and_active_diagonal_assault(self) -> None:
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        rangers = (
            ranger((595, 600), RANGER_ID),
            ranger((595, 602), RANGER_TWO_ID),
            ranger((595, 604), RANGER_THREE_ID),
            ranger((595, 606), RANGER_FOURTH_ID),
        )
        target = enemy_worker((600, 600))
        turn, _ = make_turn(
            own_core=core((560, 600)),
            units=rangers,
            enemies=(target,),
        )
        summary = tactic.choose_actions(turn)

        self.assertTrue(
            any("ranger:assault_squad" in decision for decision in summary.decisions),
            summary.decisions,
        )
        assault_routes = [
            tactic.memory.current_routes[str(unit.id)]
            for unit in rangers[2:]
            if str(unit.id) in tactic.memory.current_routes
        ]
        self.assertEqual(len(assault_routes), 2, summary.decisions)
        self.assertEqual(assault_routes[0].goal, assault_routes[1].goal)
        assert assault_routes[0].goal is not None
        self.assertEqual(
            (
                abs(assault_routes[0].goal[0] - target.position[0]),
                abs(assault_routes[0].goal[1] - target.position[1]),
            ),
            (3, 3),
        )
        self.assertTrue(
            all(
                "ranger_assault_45" in tactic.memory.current_routes[str(unit.id)].reason
                for unit in rangers[2:]
            )
        )


class CoreReserveFloorTests(unittest.TestCase):
    """机制三：Core 战备存底 150 + 产能兜底（每 tick 现场算，不硬编码人口）。"""

    @staticmethod
    def _turn_with(rk: int, wk: int, vg: int, resources: int, enemies=()):
        units = []
        for i in range(vg):
            units.append(vanguard((700 + i, 700), UUID(int=0xF000 + i)))
        for i in range(rk):
            units.append(ranger((610 + i, 600), UUID(int=0xF100 + i)))
        for i in range(wk):
            units.append(worker(UUID(int=0xF200 + i), (590 - i, 600)))
        turn, _ = make_turn(
            own_core=core((600, 600)),
            units=tuple(units),
            resources=resources,
            enemies=tuple(enemies),
        )
        return turn

    def test_growth_phase_ignores_floor(self) -> None:
        # pop=20:capacity=100,100-150<游侠价16(第21个单位起涨1.3倍) → 兜底
        # 规则生效,无视存底。resources=17(>16)即造兵,不为 150 攒钱——否则
        # 人口卡死在爬坡期。
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        turn = self._turn_with(rk=14, wk=5, vg=1, resources=17)
        pick = tactic._select_spawn_with_source(turn, 17)[0]
        self.assertIsNotNone(pick)

    def test_growth_phase_cap30_still_builds(self) -> None:
        # pop=30:capacity=150,150-150=0 < 游侠价26 → 兜底继续造,不死锁。
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        turn = self._turn_with(rk=21, wk=8, vg=1, resources=30)
        pick = tactic._select_spawn_with_source(turn, 30)[0]
        self.assertIsNotNone(pick)

    def test_mature_phase_holds_floor_when_poor(self) -> None:
        # pop=45:capacity=225,225-150=75 ≥ 游侠价58 → 存底生效。
        # resources=180 → budget=30 < 58 → 攒钱不造(保住 150 医疗预算)。
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        turn = self._turn_with(rk=33, wk=11, vg=1, resources=180)
        pick = tactic._select_spawn_with_source(turn, 180)[0]
        self.assertIsNone(pick)

    def test_mature_phase_builds_above_floor(self) -> None:
        # pop=45:resources=220 → budget=70 ≥ 58 → 超出存底的部分造兵。
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        turn = self._turn_with(rk=33, wk=11, vg=1, resources=220)
        pick = tactic._select_spawn_with_source(turn, 220)[0]
        self.assertIsNotNone(pick)

    def test_combat_yields_floor(self) -> None:
        # 存底期但近敌(T4 战时):存底让位,只留 2 战时保留。
        # resources=60:预算 58 ≥ 58 → 造兵,不为 150 攒钱。
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        turn = self._turn_with(
            rk=33, wk=11, vg=1, resources=60, enemies=(enemy_vanguard((604, 600)),)
        )
        pick = tactic._select_spawn_with_source(turn, 60)[0]
        self.assertIsNotNone(pick)


class EnemyTrailAndAxisMissTests(unittest.TestCase):
    """机制一：敌方 7 帧轨迹库 + ZIGZAG 识别 + 按轴脱靶聚合。"""

    def test_trail_records_only_positions_on_move(self) -> None:
        # 轨迹库只在敌方换格时追加；卡格不动不重复记。
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        positions = [(600, 600), (600, 600), (601, 600)]
        for tick, pos in enumerate(positions, start=1):
            turn, _ = make_turn(
                tick=tick,
                own_core=core((560, 600)),
                enemies=(enemy_vanguard(pos),),
            )
            tactic.memory.observe(turn)
        (trail,) = tactic.memory.enemy_trails.values()
        self.assertEqual(trail, [(600, 600), (601, 600)])

    def test_trail_window_caps_at_seven(self) -> None:
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        for tick in range(10):
            turn, _ = make_turn(
                tick=tick + 1,
                own_core=core((560, 600)),
                enemies=(enemy_vanguard((600 + tick, 600)),),
            )
            tactic.memory.observe(turn)
        (trail,) = tactic.memory.enemy_trails.values()
        self.assertEqual(len(trail), 7)

    def test_trail_cleared_when_enemy_leaves_vision(self) -> None:
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        seen, _ = make_turn(
            tick=1,
            own_core=core((560, 600)),
            enemies=(enemy_vanguard((600, 600)),),
        )
        tactic.memory.observe(seen)
        self.assertEqual(len(tactic.memory.enemy_trails), 1)
        gone, _ = make_turn(
            tick=2,
            own_core=core((560, 600)),
        )
        tactic.memory.observe(gone)
        self.assertEqual(tactic.memory.enemy_trails, {})

    def test_zigzag_pattern_detected_for_oscillating_vanguard(self) -> None:
        # 上下往返：(600,600)(600,601)(600,600)(600,601)(600,600) → ZIGZAG。
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        path = [(600, 600), (600, 601), (600, 600), (600, 601), (600, 600)]
        for tick, pos in enumerate(path, start=1):
            turn, _ = make_turn(
                tick=tick,
                own_core=core((560, 600)),
                enemies=(enemy_vanguard(pos),),
            )
            tactic.memory.observe(turn)
        enemy = enemy_vanguard((600, 600))
        self.assertEqual(tactic._enemy_motion_pattern(enemy), "ZIGZAG")

    def test_linear_pattern_detected_for_straight_flight(self) -> None:
        # 连续同向 ≥3 步 → LINEAR。
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        path = [
            (600, 600),
            (601, 600),
            (602, 600),
            (603, 600),
            (604, 600),
        ]
        for tick, pos in enumerate(path, start=1):
            turn, _ = make_turn(
                tick=tick,
                own_core=core((560, 600)),
                enemies=(enemy_vanguard(pos),),
            )
            tactic.memory.observe(turn)
        enemy = enemy_vanguard((604, 600))
        self.assertEqual(tactic._enemy_motion_pattern(enemy), "LINEAR")

    def test_unknown_pattern_for_short_trail(self) -> None:
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        enemy = enemy_vanguard((600, 600))
        self.assertEqual(tactic._enemy_motion_pattern(enemy), "UNKNOWN")

    def test_predicted_cell_switches_axis_under_zigzag(self) -> None:
        # ZIGZAG 时不再沿最近移动方向外推（那正是"永远瞄错方向"的根因），
        # 而是切到对轴。敌人在 (600,601)→(600,600) 之间上下往返，当前在
        # (600,600)：外推切向 x 轴。
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        path = [(600, 600), (600, 601), (600, 600), (600, 601), (600, 600)]
        for tick, pos in enumerate(path, start=1):
            turn, _ = make_turn(
                tick=tick,
                own_core=core((560, 600)),
                enemies=(enemy_vanguard(pos),),
            )
            tactic.memory.observe(turn)
        enemy = enemy_vanguard((600, 600))
        predicted = tactic._predicted_enemy_cell(turn, enemy)
        self.assertEqual(predicted[0], 601)
        self.assertEqual(predicted[1], 600)

    def test_axis_miss_penalizes_repeated_axis(self) -> None:
        # 同一轴连开两枪（未中）后，候选应偏向另一轴的格子。
        from arena_hero_strategy import _shot_axis_key, _shot_cell_key

        memory = TacticMemory()
        tactic = SmartTactic(memory)
        enemy = enemy_vanguard((600, 600))
        # 游侠在西南对角 (598,599)：到 x 轴格 (599,600) 是 1 格对角线、
        # 到 y 轴格 (600,601) 是 2 格对角线，两条轴的格子都合法可射。
        own = ranger((598, 599), UUID(int=0xB020))
        turn, _ = make_turn(
            tick=1,
            own_core=core((560, 600)),
            units=(own,),
            enemies=(enemy,),
        )
        # 对 x 轴（东西向）连记两次脱靶，并让 cell 级脱靶激活 coverage。
        memory.axis_miss_counts[_shot_axis_key(enemy.id, enemy.position, (599, 600))] += 1
        memory.axis_miss_counts[_shot_axis_key(enemy.id, enemy.position, (601, 600))] += 1
        memory.shot_miss_counts[_shot_cell_key(enemy.id, enemy.position)] = 1
        planner = MovementPlanner(turn, memory, [])
        candidates = tactic._ranger_shot_candidates(turn, turn.unit(own.id), planner)
        self.assertTrue(candidates)
        _, chosen_cell = candidates[0]
        chosen_axis = _shot_axis_key(enemy.id, enemy.position, chosen_cell)
        self.assertEqual(chosen_axis, f"{enemy.id}|y")

    def test_axis_miss_cleared_on_hit(self) -> None:
        # SHOT_HIT 清零该目标的按轴计数。
        hit = ResolutionEvent(
            event_id=UUID(int=1),
            tick=9,
            event_type="SHOT_HIT",
            reason_code=None,
            actor_id=RANGER_ID,
            target_id=ENEMY_RANGER_ID,
            position=(600, 600),
            values=None,
        )
        memory = TacticMemory()
        memory.axis_miss_counts[f"{ENEMY_RANGER_ID}|x"] = 3
        memory.axis_miss_counts[f"{ENEMY_RANGER_ID}|y"] = 1
        turn, _ = make_turn(
            tick=10,
            own_core=core((560, 600)),
            events=(hit,),
        )
        memory.observe(turn)
        self.assertEqual(memory.axis_miss_counts[f"{ENEMY_RANGER_ID}|x"], 0)
        self.assertEqual(memory.axis_miss_counts[f"{ENEMY_RANGER_ID}|y"], 0)

    def test_write_stats_exposes_attack_counters(self) -> None:
        # 真实 write_stats 顶层暴露 shots_fired / shots_hit / standoff_engagements
        # / blind_fires，取值来自 decision_totals / event_totals（已持久化）。
        import json
        import tempfile

        hit = ResolutionEvent(
            event_id=UUID(int=1),
            tick=9,
            event_type="SHOT_HIT",
            reason_code=None,
            actor_id=RANGER_ID,
            target_id=ENEMY_RANGER_ID,
            position=(600, 600),
            values=None,
        )
        memory = TacticMemory()
        memory.decision_totals["ranger:shot"] = 7
        memory.decision_totals["ranger:standoff_engaged"] = 3
        memory.decision_totals["ranger:blind_fire"] = 2
        turn, _ = make_turn(
            tick=10,
            own_core=core((560, 600)),
            events=(hit,),
        )
        memory.observe(turn)  # 累加 SHOT_HIT 进 event_totals
        with tempfile.TemporaryDirectory() as d:
            stats_path = Path(d) / "stats.json"
            memory.write_stats(stats_path, turn)
            payload = json.loads(stats_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["shots_fired"], 7)
        self.assertEqual(payload["shoot_count"], 7)  # 旧字段保留
        self.assertEqual(payload["shots_hit"], 1)
        self.assertEqual(payload["standoff_engagements"], 3)
        self.assertEqual(payload["blind_fires"], 2)


class ScoringPreaimAndSoloKillTests(unittest.TestCase):
    """打分制预瞄 + 泛化相持检测 + 游侠单杀先锋舞步。"""

    def _observe_standoff_warmup(
        self, tactic: SmartTactic, enemy_pos=(603, 600), own_ranger_pos=(600, 600),
        obstacle=(602, 598),
    ) -> None:
        """用 4 帧建立敌游侠"原地僵持"轨迹。"""
        for tick in range(4):
            warm, _ = make_turn(
                tick=tick + 1,
                own_core=core((560, 600)),
                units=(ranger(own_ranger_pos),),
                enemies=(enemy_ranger(enemy_pos),),
                obstacle_cells=(obstacle,) if obstacle else (),
            )
            tactic.memory.observe(warm)

    def test_score_aim_cells_low_hp_enemy_prefers_backward(self) -> None:
        # 敌游侠 hp1 上一步 RIGHT → BACKWARD(LEFT) 分最高。
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        enemy = enemy_ranger((600, 600), hp=1)
        own = ranger((598, 600), UUID(int=0xB020))
        turn, _ = make_turn(
            tick=1, own_core=core((560, 600)),
            units=(own,), enemies=(enemy,),
        )
        tactic.memory.observe(turn)
        # 再观察一帧让敌游侠"上一步向右"(把 prev 设成(599,600),current(600,600)=向右一步)。
        enemy2 = enemy_ranger((601, 600), hp=1)
        turn2, _ = make_turn(
            tick=2, own_core=core((560, 600)),
            units=(ranger((598, 600), UUID(int=0xB020)),), enemies=(enemy2,),
        )
        tactic.memory.observe(turn2)
        planner = MovementPlanner(turn2, memory, [])
        scored = tactic._score_aim_cells(turn2, enemy2, planner)
        score_by_cell = {cell: s for cell, s in scored}
        # 上一步向右(+x),BACKWARD = 向左格(600,600)。
        backward_cell = (600, 600)
        # 比 FORWARD(602,600)、LATERAL(601,599/601,601) 都高。
        self.assertIn(backward_cell, score_by_cell)
        for cell, s in scored:
            if cell != backward_cell:
                self.assertGreater(
                    score_by_cell[backward_cell], s,
                    f"backward {backward_cell} 应最高,但 {cell}={s} 更高",
                )

    def test_score_aim_cells_ambush_ranger_locks_current_cell(self) -> None:
        # 满血敌游侠(hp2)主动步入我射线 → STAY=原位 分最高 → 换血。
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        # prev(603,600) → current(602,600):向左一步,且靠近我游侠(599,600)(距4→3)。
        enemy_prev_turn, _ = make_turn(
            tick=1, own_core=core((560, 600)),
            units=(ranger((599, 600), UUID(int=0xB020)),),
            enemies=(enemy_ranger((603, 600), hp=2),),
        )
        tactic.memory.observe(enemy_prev_turn)
        enemy = enemy_ranger((602, 600), hp=2)
        turn, _ = make_turn(
            tick=2, own_core=core((560, 600)),
            units=(ranger((599, 600), UUID(int=0xB020)),),
            enemies=(enemy,),
        )
        tactic.memory.observe(turn)
        planner = MovementPlanner(turn, memory, [])
        scored = tactic._score_aim_cells(turn, enemy, planner)
        score_by_cell = {cell: s for cell, s in scored}
        stay_cell = enemy.position  # (602,600)
        self.assertIn(stay_cell, score_by_cell)
        for cell, s in scored:
            if cell != stay_cell:
                self.assertGreaterEqual(
                    score_by_cell[stay_cell], s,
                    f"ambush STAY {stay_cell} 应最高(平手也过)",
                )

    def test_score_aim_cells_dampening_lowers_missed_direction(self) -> None:
        # x 轴连记脱靶 → x 轴格分低于 y 轴格。
        from arena_hero_strategy import _shot_axis_key, _shot_cell_key

        memory = TacticMemory()
        tactic = SmartTactic(memory)
        enemy = enemy_vanguard((600, 600))
        own = ranger((598, 599), UUID(int=0xB020))
        turn, _ = make_turn(
            tick=1, own_core=core((560, 600)),
            units=(own,), enemies=(enemy,),
        )
        memory.axis_miss_counts[_shot_axis_key(enemy.id, enemy.position, (599, 600))] += 1
        memory.axis_miss_counts[_shot_axis_key(enemy.id, enemy.position, (601, 600))] += 1
        memory.shot_miss_counts[_shot_cell_key(enemy.id, enemy.position)] = 1
        planner = MovementPlanner(turn, memory, [])
        scored = tactic._score_aim_cells(turn, enemy, planner)
        score_by_cell = {cell: s for cell, s in scored}
        x_cell = (599, 600)
        y_cell = (600, 601)
        if x_cell in score_by_cell and y_cell in score_by_cell:
            self.assertGreater(
                score_by_cell[y_cell], score_by_cell[x_cell],
                "y 轴格应因 x 轴脱靶降分而更高",
            )

    def test_detect_standoff_vanguard_cornered(self) -> None:
        # 敌先锋 hp2 + 两友游侠 ≤2 格 → kind=vanguard_cornered。
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        turn, _ = make_turn(
            tick=1, own_core=core((560, 600)),
            units=(
                ranger((601, 600), UUID(int=0xB030)),
                ranger((600, 601), UUID(int=0xB031)),
            ),
            enemies=(enemy_vanguard((600, 600), hp=2),),
        )
        standoff = tactic._detect_strategic_standoff(turn)
        self.assertIsNotNone(standoff)
        self.assertEqual(standoff.kind, "vanguard_cornered")
        self.assertEqual(standoff.original_cell, (600, 600))

    def test_detect_standoff_worker_fleeing(self) -> None:
        # 敌工人 + 障碍堵 2 cardinal + 友游侠 ≤3 → kind=worker_fleeing。
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        # 工人轨迹:上一步(600,600) → 当前(601,600) 远离 Core(560,600)。
        warm, _ = make_turn(
            tick=1, own_core=core((560, 600)),
            units=(ranger((602, 600), UUID(int=0xB030)),),
            enemies=(enemy_worker((600, 600)),),
            obstacle_cells=((600, 601), (601, 601)),  # 堵 2 个 cardinal
        )
        tactic.memory.observe(warm)
        turn, _ = make_turn(
            tick=2, own_core=core((560, 600)),
            units=(ranger((602, 600), UUID(int=0xB030)),),
            enemies=(enemy_worker((601, 600)),),
            obstacle_cells=((600, 601), (601, 601)),
        )
        tactic.memory.observe(turn)
        standoff = tactic._detect_strategic_standoff(turn)
        self.assertIsNotNone(standoff)
        self.assertEqual(standoff.kind, "worker_fleeing")

    def test_diagonal_support_ranger_fires_at_original_cell(self) -> None:
        # 建 4 帧相持 + 第三满血游侠站盲区对角格 → 射敌原位 + 计数。
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        self._observe_standoff_warmup(tactic)
        # 第三游侠站在盲区对角格(600,597):warmup 用 obstacle (602,598) 造盲区。
        relay_view = ranger((600, 597), UUID(int=0xB040))
        turn, _ = make_turn(
            tick=5, own_core=core((560, 600)),
            units=(ranger((600, 600)), relay_view),
            enemies=(enemy_ranger((603, 600)),),
            obstacle_cells=((602, 598),),
        )
        summary = tactic.choose_actions(turn)
        action = turn.plan.unit_actions.get(relay_view.id)
        self.assertIsNotNone(action, summary.decisions)
        self.assertIsInstance(action, ShootAction)
        assert isinstance(action, ShootAction)
        # 支援游侠射敌原位(603,600),非预测格。
        self.assertEqual(action.expected_cell, (603, 600))
        self.assertEqual(
            memory.decision_totals["ranger:diagonal_support"], 1
        )

    def test_diagonal_support_does_not_fire_out_of_range(self) -> None:
        # 回归:支援游侠被指派但站位超射程(距敌原位 6 格,远超游侠射程 3)时,
        # 不得提交超距 SHOOT(否则服务器判 SHOT_MISSED——看得见命中、实际无效)。
        # 必须回落到 standoff_relay_advance 推进分支继续走近,而非原地开空枪。
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        self._observe_standoff_warmup(tactic)
        # 第三游侠站在 (597,597),距敌原位 (603,600) = max(6,3)=6,既超射程又非 45°对齐。
        far_ranger = ranger((597, 597), UUID(int=0xB041))
        turn, _ = make_turn(
            tick=5, own_core=core((560, 600)),
            units=(ranger((600, 600)), far_ranger),
            enemies=(enemy_ranger((603, 600)),),
            obstacle_cells=((602, 598),),
        )
        summary = tactic.choose_actions(turn)
        action = turn.plan.unit_actions.get(far_ranger.id)
        # 不得是 ShootAction(超射程开火即无效命中)。
        self.assertNotIsInstance(
            action, ShootAction,
            f"超射程支援游侠不应开火,实际动作={action!r};决策={summary.decisions}",
        )
        self.assertEqual(
            memory.decision_totals["ranger:diagonal_support"], 0,
            f"超射程不应计 diagonal_support;决策={summary.decisions}",
        )
        # 应正在推进向换血位(standoff_relay_advance),或至少不是原地无效开火。
        self.assertTrue(
            any("standoff_relay_advance" in line for line in summary.decisions),
            f"应推进向换血位;决策={summary.decisions}",
        )

    def test_diagonal_support_abandons_dead_chase_and_releases_ranger(self) -> None:
        # 死追保护:支援游侠连续多 tick 被指派却到不了合法射击线(敌每 tick 移动→
        # relay_cell 跟着移)→ 达阈值后冷却该游侠、回落常规分支(动起来),不再死追。
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        self._observe_standoff_warmup(tactic)
        far_ranger = ranger((570, 590), UUID(int=0xB042))
        # 预置:该游侠已连续未开火到阈值-1,再走一帧即触发放弃。
        from arena_hero_strategy import STANDOFF_SUPPORT_MAX_STALL_TICKS
        memory.standoff_support_stall[str(far_ranger.id)] = STANDOFF_SUPPORT_MAX_STALL_TICKS - 1
        # 敌游侠仍在(603,600)原地相持(relay_cell=(600,597),游侠(570,590)远在外)。
        turn, _ = make_turn(
            tick=5, own_core=core((560, 600)),
            units=(ranger((600, 600)), far_ranger),
            enemies=(enemy_ranger((603, 600)),),
            obstacle_cells=((602, 598),),
        )
        summary = tactic.choose_actions(turn)
        # 应触发放弃并写入冷却。
        self.assertTrue(
            any("standoff_relay_abandon" in line for line in summary.decisions),
            f"应触发死追放弃;决策={summary.decisions}",
        )
        self.assertIn(str(far_ranger.id), memory.standoff_support_cooldown)
        # 放弃后本 tick 仍有动作(动起来),不得是空 wait——回落巡逻/作战分支。
        action = turn.plan.unit_actions.get(far_ranger.id)
        self.assertIsNotNone(action, f"放弃后应有动作;决策={summary.decisions}")
        # 放弃的 tick 不应再是 standoff_relay_advance(已让出指派)。
        for line in summary.decisions:
            if _short_id(far_ranger.id) in line:
                self.assertNotIn("standoff_relay_advance", line)

    def test_vanguard_dance_approach_gap_aims_gap_cell(self) -> None:
        # 我游侠(600,600) 敌先锋 hp4(602,600) 上一步朝我 → APPROACH_GAP 射 gap(601,600)。
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        # 先观察 warmup 帧让先锋 prev=(603,600);choose_actions 会 observe 当前帧。
        warm, _ = make_turn(
            tick=1, own_core=core((560, 600)),
            units=(ranger((600, 600), RANGER_ID),),
            enemies=(enemy_vanguard((603, 600), hp=4),),
        )
        tactic.memory.observe(warm)
        turn, _ = make_turn(
            tick=2, own_core=core((560, 600)),
            units=(ranger((600, 600), RANGER_ID),),
            enemies=(enemy_vanguard((602, 600), hp=4),),
        )
        # 不预 observe(turn):choose_actions 内部会 observe,保留 warmup 的 prev。
        summary = tactic.choose_actions(turn)
        own = turn.unit(RANGER_ID)
        action = turn.plan.unit_actions.get(own.id)
        self.assertIsNotNone(action, summary.decisions)
        self.assertIsInstance(action, ShootAction)
        assert isinstance(action, ShootAction)
        self.assertEqual(action.expected_cell, (601, 600))
        self.assertGreaterEqual(memory.decision_totals["ranger:vanguard_dance"], 1)
        # phase 推进到 ADJACENT_BACK。
        pair_key = f"{RANGER_ID}|{enemy_vanguard((602, 600)).id}"
        self.assertEqual(
            memory.vanguard_dance_phase[pair_key]["phase"], "ADJACENT_BACK"
        )

    def test_vanguard_dance_adjacent_back_steps_away(self) -> None:
        # seed phase=ADJACENT_BACK、贴脸 hp4 → action 为朝先锋反方向的 MoveAction。
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        own = ranger((600, 600), RANGER_ID)
        enemy = enemy_vanguard((601, 600), hp=4)
        pair_key = f"{RANGER_ID}|{enemy.id}"
        memory.vanguard_dance_phase[pair_key] = {"phase": "ADJACENT_BACK"}
        turn, _ = make_turn(
            tick=1, own_core=core((560, 600)),
            units=(own,), enemies=(enemy,),
        )
        # 不预 observe:phase 已 seed,choose_actions 内部 observe 即可。
        summary = tactic.choose_actions(turn)
        action = turn.plan.unit_actions.get(own.id)
        # 先锋在我游侠右侧(601,600)→游侠后退向 LEFT。
        self.assertIsNotNone(action, summary.decisions)
        self.assertIsInstance(action, MoveAction)
        assert isinstance(action, MoveAction)
        self.assertEqual(action.direction, Direction.LEFT)
        self.assertGreaterEqual(memory.decision_totals["ranger:vanguard_dance"], 1)

    def test_vanguard_dance_hp2_preaims_retreat(self) -> None:
        # phase=REAIM_GAP_HP2、先锋 hp2 → 射击 expected_cell 为撤退方向格。
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        own = ranger((600, 600), RANGER_ID)
        # 先锋在(601,600)贴脸 hp2,prev=(602,600)(上一步朝我,撤退方向=向右)。
        enemy = enemy_vanguard((601, 600), hp=2)
        pair_key = f"{RANGER_ID}|{enemy.id}"
        memory.vanguard_dance_phase[pair_key] = {"phase": "REAIM_GAP_HP2"}
        warm, _ = make_turn(
            tick=1, own_core=core((560, 600)),
            units=(own,), enemies=(enemy_vanguard((602, 600), hp=2),),
        )
        tactic.memory.observe(warm)
        turn, _ = make_turn(
            tick=2, own_core=core((560, 600)),
            units=(own,), enemies=(enemy,),
        )
        # 不预 observe(turn):choose_actions 内部 observe 会把 prev 设成 warm 的 (602,600)。
        summary = tactic.choose_actions(turn)
        action = turn.plan.unit_actions.get(own.id)
        self.assertIsNotNone(action, summary.decisions)
        self.assertIsInstance(action, ShootAction)
        assert isinstance(action, ShootAction)
        # 撤退方向(向右,远离我游侠)= (602,600)。
        self.assertEqual(action.expected_cell, (602, 600))

    def test_vanguard_dance_flee_ambush_triggers_cluster_preaim(self) -> None:
        # 游侠 hp1(先锋反攻) → FLEE_AMBUSH 预标记 + 第二游侠射追兵 + ambush_trade≥1。
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        own_injured = ranger((600, 600), RANGER_ID, hp=1)
        enemy = enemy_vanguard((601, 600), hp=2)
        pair_key = f"{RANGER_ID}|{enemy.id}"
        memory.vanguard_dance_phase[pair_key] = {"phase": "REAIM_GAP_HP2"}
        # 第二游侠站能射先锋追兵的位置:(601,599) 竖距1 射 (601,600)。
        support = ranger((601, 599), UUID(int=0xB050))
        turn, _ = make_turn(
            tick=1, own_core=core((560, 600)),
            units=(own_injured, support), enemies=(enemy,),
        )
        # 不预 observe:choose_actions 内部 observe + FLEE_AMBUSH 预标记会读 hp。
        tactic.choose_actions(turn)
        # FLEE_AMBUSH 预标记 + 第二游侠射追兵 + ambush_trade≥1。
        self.assertGreaterEqual(memory.decision_totals["ranger:ambush_trade"], 1)


class AgentLifecycleStatusTests(unittest.TestCase):
    def test_status_file_is_atomic_credential_free_and_carries_session(self) -> None:
        with TemporaryDirectory() as directory_name:
            path = Path(directory_name) / "nested" / "status.json"
            previous_path = os.environ.get("ARENA_HERO_AGENT_STATUS_FILE")
            previous_session = os.environ.get("ARENA_HERO_AGENT_SESSION_ID")
            previous_key = os.environ.get("ARENA_HERO_API_KEY")
            os.environ["ARENA_HERO_AGENT_STATUS_FILE"] = str(path)
            os.environ["ARENA_HERO_AGENT_SESSION_ID"] = "test-session"
            os.environ["ARENA_HERO_API_KEY"] = "must-not-leak"
            try:
                _write_agent_status("running", detail="AuthenticationError")
            finally:
                if previous_path is None:
                    os.environ.pop("ARENA_HERO_AGENT_STATUS_FILE", None)
                else:
                    os.environ["ARENA_HERO_AGENT_STATUS_FILE"] = previous_path
                if previous_session is None:
                    os.environ.pop("ARENA_HERO_AGENT_SESSION_ID", None)
                else:
                    os.environ["ARENA_HERO_AGENT_SESSION_ID"] = previous_session
                if previous_key is None:
                    os.environ.pop("ARENA_HERO_API_KEY", None)
                else:
                    os.environ["ARENA_HERO_API_KEY"] = previous_key

            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["state"], "running")
            self.assertEqual(payload["session_id"], "test-session")
            self.assertEqual(payload["detail"], "AuthenticationError")
            self.assertNotIn("must-not-leak", path.read_text(encoding="utf-8"))
            self.assertFalse(path.with_suffix(".json.tmp").exists())


if __name__ == "__main__":
    unittest.main()
