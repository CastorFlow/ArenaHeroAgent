from __future__ import annotations

import heapq
import json
import logging
import math
import os
import time
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from uuid import UUID

from arena_hero import (
    BeaconStatus,
    CoreState,
    CoreView,
    Direction,
    HarvestSource,
    Ranger,
    Turn,
    Unit,
    UnitType,
    UnitView,
    Vanguard,
    Worker,
    unit_cost,
)


Position = tuple[int, int]
Chunk = tuple[int, int]
CHUNK_SIZE = 32
ROUTES_FILENAME = ".arena_hero_routes.json"
RECOVERY_TARGETS_FILENAME = ".arena_hero_recovery_targets.json"
CONTROL_FILENAME = ".arena_hero_control.json"
STATS_FILENAME = ".arena_hero_stats.json"
BROWSER_INTEL_FILENAME = ".arena_hero_browser_intel.json"
ROUTE_OVERLAY_VERSION = 2
# 战况历史 / Core 轨迹 JSONL 滚动上限（行）。tick≈15s，10 万行 ≈ 17 天战况；
# Core 轨迹 1 万行 ≈ 41 天。
BATTLE_HISTORY_MAX_LINES = 100_000
CORE_TRAIL_MAX_LINES = 10_000

MODE_LIGHTNING = "lightning"
# === 网页控制台控制字段（与 arena_hero_route_overlay_server 的 schema 保持一致）===
# Core 转移模式：star=恒星(边采集边交付), march=急行军(停止采集随 Core 推进),
# fortify=坚壁清野(采集但不提交,到达目标后集中提交)。
TRANSFER_MODES = {"star", "march", "fortify"}
CONTROL_UNIT_TYPES = ("WORKER", "VANGUARD", "RANGER")
MAX_BUILD_QUEUE_LENGTH = 20

# 闪电模式：Core 沿半径 core_orbit_radius 的方形周界（中心原点、边长 2r）四角
# 轮转巡逻。r=0 表示未设置，Core 不巡逻（停在原地）。手动改 r 会触发恒星模式
# 迁移：把 core_target 设为新方形周界上离 Core 最近的点，走 star/march/fortify
# 转移状态机，到达后在新轨道上继续巡逻。
# 战斗单位各自独立路线扫场，不组队。
# 巡逻点沿半径 r 的方形周界四角轮转；到位（进入 CORE_BEACON_HYSTERESIS 死区）后换下一角。
# 闪电模式常驻兵力软顶(经济软天花板)。100~110 是软天花板:游侠单价随 pop 涨价
# (k=max(0,floor((pop-20)/5)+1, 单价=round(base×1.3^k)),pop100 时游侠≈1038、容量 500,
# 造一个要攒 2+ tick,极慢但不停产。3:1 产能(游侠:工人)在此区间继续运作。
LIGHTNING_MAX_POPULATION = 100
# 绝对人口硬上限(兜底):比软顶多 5,防意外溢出。到此后不再产兵。
ABSOLUTE_MAX_POPULATION = 105
# Core 战备资源存底(用户定稿 2026-08-14):和平期保底 150 资源,留给战时给
# 受伤士兵回血与阵亡补兵。仅当 资源上限-150 ≥ 当前游侠价 时生效(人口 ≥ ~40);
# 爬坡期上限装不下"150+一个游侠",无视存底继续造兵抬上限,避免永久卡死。
CORE_WARTIME_RESOURCE_FLOOR = 150
# 先锋敌方 Core 猎杀链已废弃(原 LIGHTNING_HUNT_*/CROWD/FOCUS_MAX_ATTACKERS/SIGHTING_MAX_AGE
# 常量随 _lightning_acquire_target 等函数一并删除):先锋只守近轨,不再被远方敌 Core 吸走。

# === 绕银河多层轨道体系 ===
# Core 轨道（恒星绕银心）绕原点 (0,0) 转 pr≈450 方环，慢；游侠与工人共享中行星轨道
#   （绕 Core 转圈），不再有开路/远行星轨道。
# 行星轨道层序(内→外):先锋(近行星,半径 LIGHTNING_NEAR_ORBIT_RADIUS=5，
#   贴 Core 视野边缘) → 游侠+工人(中行星，单一有序队列，游侠占内层、工人接外层)。见
#   _lightning_assign_shared_middle_lanes。游侠护 Core 中层、工人外层点亮外围迷雾。
# 局部威胁感知半径：游侠/先锋执行轨道巡逻时，检测周围此半径内的敌方战斗单位。
# 发现威胁时执行局部避战（暂停巡逻，撤向 Core 或绕开），防止孤军深入被围杀。
LIGHTNING_LOCAL_THREAT_RADIUS = 8
# 哈雷彗星小队威胁感知半径：派往信标/自定义坐标的成员，在距敌方战斗单位此距离内
# 不得继续无脑推进。敌方游侠射程 3、先锋近战 1，取 3 作统一警戒圈——
# 进到这个圈里再往前就等于一头扎进敌方火力射程送。
COMET_THREAT_RADIUS = 3
# 彗星游侠还击射程上限（=游侠射程），用于就近挑能打到的敌方战斗单位先开火清障。
COMET_RANGER_FIRE_RANGE = 3
# 集合点"到达"判定半径：成员所在格距集合点曼哈顿距离 ≤ 此值即算到达集合点
# 近旁（集合点本身可能是障碍物，成员绕其周围近邻占位即可）。1 = 贴集合点周围
# 一圈；因集合点常为障碍/不可占格，取 1 让成员在集合点相邻格散开占位警戒。
COMET_RALLY_ARRIVE_RADIUS = 1
# 造兵系统统一规则（无固定前置阶梯；仅开局引导：前 3 个造工人，之后一视同仁）：
#   优先级 = 预定队列 → 开局引导(前3工人) → 补兵阈值/补兵优先级规则
#   → 各兵种独立上限 → 正常比例趋近。
#   参见 _select_spawn_with_source / _lightning_ratio_spawn。
# 各 role 行星子轨道径向间距起步 = 该角色视野半径（相邻两层视野恰好相切，
# 覆盖连续不重叠）。后续按产能/盲区情况调。
LIGHTNING_ORBIT_LANE_GAP_RADIUS: dict[UnitType, int] = {
    UnitType.VANGUARD: 4,
    UnitType.WORKER: 3,
    UnitType.RANGER: 5,
}
# 基于周长分配的理想单位间距（格）：在视野覆盖和响应速度间平衡。
# 间距过小（<视野直径）→ 视野重叠浪费；间距过大（>视野*2）→ 盲区大、单位稀疏。
# 设定原则：间距 ≈ 视野直径 * 1.5~2.0（适度盲区换取更大防御圈）。
LIGHTNING_IDEAL_INTERVAL: dict[UnitType, int] = {
    UnitType.VANGUARD: 8,   # 视野4, 间距8 (2倍，近战需密集)
    UnitType.RANGER: 10,    # 视野5, 间距10 (2倍，平衡覆盖与数量)
    UnitType.WORKER: 6,     # 视野3, 间距6 (2倍，工人主要经济不需全覆盖)
}
# 每层轨道最少单位数（铺开领土阶段）：从2提到3，减少外层稀疏盲区。
LIGHTNING_MIN_UNITS_PER_ORBIT = 3
# 行星轨道叠格死区（到点位多久推下一点位）沿用 CORE_BEACON_HYSTERESIS=8。
# === 轨道点位环（替代软斥力，防同层扎堆）===
# 同层扎堆的根源：目标只有 4 个角，同层 >4 时必有单位共享角点且同节奏推进 → 永远
# 贴在一起绕圈。改为给每层铺设"点位环"——沿方形周界均匀放 M 个点位（M=next_pow2(层
# 单位数)，至少 4 个角），每单位按 bit-reversal 序认领一个**互不相同**的角/中点作起点
# （2 单位→对角、4 单位→四角、5+→补边中点，正好是用户要的"对角落位→逐级细分"）。
# 单位沿环同向逐点位扫过去；到点死区推进、乱石堆提前跳点位、"目标点位附近有同环友军
# → 跳过（反扎堆超车）"。点位间距（≈周长/M）远大于 2*ALLY_RADIUS，故一跳之内必找到
# 空位，追上瞬间变成干净超车而非粘住。战斗移动（拦截/狙击/布防/撤退）priority 完全
# 不动——点位只是纯巡逻 fallback 目标，敌人进攻时该集结还是集结。
LIGHTNING_ORBIT_WAYPOINT_ALLY_RADIUS = 3   # 目标点位距同环友军此格内 → 跳过
# === 鬼打墙逃生（战斗单位/工人轨道巡逻共用）===
# 卡住检测：最近 ESCAPE_DETECT_WINDOW 个位置的活动范围（max-norm 直径）≤
# ESCAPE_DETECT_SPAN 视为一次"小范围震荡"命中；连续命中 ESCAPE_TRIGGER_HITS
# 次触发逃生模式。
LIGHTNING_ESCAPE_DETECT_WINDOW = 8
LIGHTNING_ESCAPE_DETECT_SPAN = 6  # 放宽到 6 格，覆盖大环震荡（不只是 2 格 ping-pong）
LIGHTNING_ESCAPE_TRIGGER_HITS = 3
# 卡住命中还须"当前格在窗口内重复出现 ≥ 此值"——真震荡必反复回访同一格;
# 长期停驻后刚恢复移动的单位每步都在新格(count=1),不会被停驻历史误伤。
# 降到 2：4 格环形震荡时每格在 8 位置窗口内出现 2 次，3 会漏检。
LIGHTNING_ESCAPE_REVISIT_MIN = 2
# 逃生模式持续 tick 数：期间忽略巡逻目标，只往开阔/低 visited 密度方向走。
LIGHTNING_ESCAPE_DURATION_TICKS = 20  # 延长到 20，给足时间走出复杂地形
# 逃生打分权重：开阔度（邻格出口数）优先，visited 密度次之。
# exits 权重大：优先往通道多的方向走（逃出死胡同的核心）。
# density 用对数曲线：visited 饱和后仍保持区分度，不会被巨大数值淹没。
LIGHTNING_ESCAPE_EXIT_WEIGHT = 5.0  # 从 3.0 提到 5.0，强化开阔度引导
LIGHTNING_ESCAPE_VISITED_WEIGHT = 3.0  # 逃生期单格 visited 直接惩罚，替代原 3×3 求和
# 逃生期朝 goal 的弱偏置：逃生本质是脱出死胡同，仍以"开阔度优先 + 避开走烂区"为绝对
# 主导(EXIT 5.0/出口、VISITED 3.0)。但在两候选格 exits/visited 相近时，逃生方向常一路
# 往内圈钻(goal_r=30 的单位逃生后 pos_r 反而 12→3，离轨道越来越远)。加一项"走此格是否
# 更靠近 goal"的弱打分(权重 1.0，远小于上述两项)：朝 goal 走 -1、背向 +1。脱困后自然把
# 单位往自己巡逻半径的方环上弯，而不压过脱困主导项。goal 仍是 _lightning_orbit_waypoint
# 的巡逻点位，"朝 goal"="朝自己轨道"，不破坏逃生的"完全忽略目标"语义(目标方向从未
# 进入触发/结束判定，只在评分并列时作决胜)。
LIGHTNING_ESCAPE_GOAL_BIAS_WEIGHT = 1.0
# 巡逻角障碍密度跳角：目标角周围 5x5（25 格）内已知障碍 > 此值（40%）时，
# 且单位距角尚远（> 死区*2），视为"角在乱石堆里"，提前推进下一角绕行。
LIGHTNING_CORNER_OBSTACLE_LIMIT = 10
# 先锋近轨是队形的种子半径；实际动态防线在 _lightning_orbit_geometry
# 中从 Ranger/Worker 的最终 shared lanes 推导，绝不使用固定 NEAR/MID/FAR 边界。
LIGHTNING_NEAR_ORBIT_RADIUS = 5
# A distant Beacon needs a head start: waiting for the complete 3+3 home
# reserve plus the full expedition can postpone first contact for thousands of
# Ticks when local resource income is sparse.  Keep Develop-mode workers on the
# economy, but release one Vanguard/Ranger scout pair once a safe 2+1 home
# screen remains.
DEVELOP_EARLY_BEACON_MIN_DISTANCE = CHUNK_SIZE * 2
DEVELOP_EARLY_BEACON_MIN_VANGUARDS = 3
DEVELOP_EARLY_BEACON_MIN_RANGERS = 2
DEVELOP_SEARCH_INITIAL_RADIUS = 10
DEVELOP_SEARCH_STEP = 8
CORE_SHELTER_MEMORY_MAX_DISTANCE = 12
# 信标目标距离控制的容差带（格）：距离偏差超过此值才迁移，避免来回抖动
CORE_BEACON_HYSTERESIS = 8
# A Core that immediately starts another four-Tick move can keep a loaded
# Worker chasing a moving deposit point. Pause only for the final approach;
# a wider radius made Beacon migrations spend most Ticks waiting for cargo.
CORE_MIGRATION_CARGO_SERVICE_RADIUS = 8
# Low-resource development stays inside the Core's local 32x32 production
# area and its nearest boundary. Longer one-way searches delay deposits and
# army rebuilding more than the extra vision helps.
DEVELOP_WIDE_SEARCH_MAX_RADIUS = 28
# A visible resource can still be a poor economic target when it was revealed
# by a distant scout.  Keep new Develop-mode assignments inside the same local
# production radius unless a Worker is already close enough to finish it.
DEVELOP_RESOURCE_TARGET_CORE_LEASH_DISTANCE = 38
# 卡住判定：单位连续这么多 tick 位置未变化且仍有移动目标 → 视为迷路
STUCK_TICKS = 16
# 打转判定：最近 STUCK_TICKS 个 tick 内，单位经过的不同位置 ≤ 此阈值 → 震荡打转
SPIN_POSITION_BUDGET = 6
# 单位满血值
MAX_HP = {UnitType.WORKER: 2, UnitType.VANGUARD: 4, UnitType.RANGER: 2}
CORE_EMERGENCY_THREAT_RADIUS = 6
# AGGRESS_CORE_ALERT_RADIUS：虽然 Aggress 模式已禁用(elif False)，但其底层函数
# _enemy_movement_anchor 仍被 Lightning 模式的游侠射击候选(via _ranger_shot_candidates)
# 和 write_stats 遥测统计调用。保留此常量以维持代码完整性。
AGGRESS_CORE_ALERT_RADIUS = 10
CORE_DAMAGE_EMERGENCY_TICKS = 24
CORE_RECOVERY_REBUILD_TICKS = 120
# 守家编制是所有外派任务的硬底线：1 先锋 + 1 游侠（原 AGGRESS_DEFENDER 编制）。
HOME_RESERVE_VANGUARDS = 1
HOME_RESERVE_RANGERS = 1
HOME_RESERVE_COMBAT = HOME_RESERVE_VANGUARDS + HOME_RESERVE_RANGERS
ASSAULT_SIGHTING_MAX_AGE = 20
CORE_VISION_RADIUS = 5
UNIT_VISION_RADIUS = {
    UnitType.WORKER: 3,
    UnitType.VANGUARD: 4,
    UnitType.RANGER: 5,
}

DIRECTION_ORDER = (
    Direction.UP,
    Direction.RIGHT,
    Direction.DOWN,
    Direction.LEFT,
)
RANGER_LINE_DELTAS = (
    (0, -1),
    (1, -1),
    (1, 0),
    (1, 1),
    (0, 1),
    (-1, 1),
    (-1, 0),
    (-1, -1),
)
DIRECTION_RANK = {direction: index for index, direction in enumerate(DIRECTION_ORDER)}
OPPOSITE_DIRECTION = {
    Direction.UP: Direction.DOWN,
    Direction.RIGHT: Direction.LEFT,
    Direction.DOWN: Direction.UP,
    Direction.LEFT: Direction.RIGHT,
}
# === 打分制预瞄方向权重基线（用户原则的数值化）===
# 敌方 agent 控制,原地概率小,STAY 基线最低。一般情形 BACKWARD>LATERAL>FORWARD>STAY。
# 各场景在 _score_aim_cells 里按 context 加性叠加覆盖。
AIM_DIRECTION_WEIGHTS: dict[str, float] = {
    "BACKWARD": 40.0,
    "LATERAL": 25.0,
    "FORWARD": 15.0,
    "STAY": 5.0,
}
# 脱靶降分系数 + 封顶（连续打不中某方向→降分,但有上限防永久放弃）。
AIM_MISS_CELL_PENALTY = 8.0
AIM_MISS_AXIS_PENALTY = 4.0
AIM_MISS_PENALTY_CAP = 50.0
# 重复覆盖格偏好（保留 ranger:shot_coverage 语义）。
AIM_COVERAGE_BONUS = 6.0
# 满血敌游侠主动入弹道(偷袭)→ STAY 锁原位换血的最高分。
AIM_AMBUSH_STAY_SCORE = 90.0
# 游侠单杀先锋舞步 kiting 接战距离(单位:格)。
VANGUARD_DANCE_ENGAGE_RADIUS = 3
# Mode B 各 phase 名。
VANGUARD_DANCE_PHASES = (
    "APPROACH_GAP",
    "ADJACENT_BACK",
    "REAIM_GAP_HP2",
    "FLEE_AMBUSH",
)
CORE_DIRECTION_COMMIT_TICKS = 8
RANGER_DEFENSE_LEASH_RADIUS = 8
CORE_PATROL_RANGER_COUNT = 2
CORE_PATROL_RADIUS = 2
CORE_PATROL_ROTATION_TICKS = 8
# 射失后的短期记忆：避免对同一敌人和同一格连续浪费行动。
RANGER_SHOT_MISS_MEMORY_TICKS = 8
# 45°支援游侠追逐移动中的 relay_cell(敌每 tick 移动→relay_cell 跟着移)可能永远到不了
# 合法射击线,陷入"standoff_relay_advance 反复推进却开不了火"的死追(被从巡逻/作战中剥离,
# 表现为贴墙不动不攻击)。连续未开火达到此阈值即冷却该游侠若干 tick,回落常规分支。
STANDOFF_SUPPORT_MAX_STALL_TICKS = 4
STANDOFF_SUPPORT_COOLDOWN_TICKS = 12
CORE_AUTO_MOBILITY_MIN_VANGUARDS = 1
CORE_AUTO_MOBILITY_MIN_RANGERS = 1
CORE_AUTO_MOBILITY_MIN_COMBAT = 2
REFILL_PROBE_MAX_DISTANCE = 40
REFILL_PROBE_BACKTRACK_DISTANCE = 12
REFILL_PROBE_CORE_LEASH_DISTANCE = 24
# 发育阶段的 refill 复查也必须服从本地经济半径。远程往返会让一个资源
# 占用工人数十 Tick，并在单入口 Core 前形成持续回仓队列。
DEVELOP_REFILL_PROBE_CORE_LEASH_DISTANCE = DEVELOP_WIDE_SEARCH_MAX_RADIUS
LAST_SEEN_RESOURCE_MAX_DISTANCE = 24
LAST_SEEN_RESOURCE_BACKTRACK_DISTANCE = 10
BROWSER_INTEL_MAX_AGE_SECONDS = 12
# A live chunk can never expose more natural resource points than its quota.
# Reject the whole browser snapshot when it violates that invariant; this
# prevents DOM/overlay parsing mistakes from masquerading as thousands of
# resource cells and dragging scouts across the map.
BROWSER_RESOURCE_REQUIRE_QUOTA_PLAUSIBILITY = True
# 浏览器地图只作为近处低可信提示；远处坐标必须由游戏视野重新确认，
# 否则一次旧快照会把所有工人拖离采集区。
BROWSER_RESOURCE_HINT_MAX_DISTANCE = 32
BROWSER_RESOURCE_SCOUT_LIMIT = 1
CORE_LOGISTICS_CORRIDOR_LENGTH = 3
MIGRATION_SITE_RADIUS = 3
MIGRATION_SITE_MAX_OPEN_RANGED_CELLS = 12
MIGRATION_SITE_MIN_OPEN_HALF_RATIO_NUMERATOR = 3
MIGRATION_SITE_MIN_OPEN_HALF_RATIO_DENOMINATOR = 4
@dataclass(frozen=True)
class CoreAnchorState(str, Enum):
    """Core movement state for the current Tick, derived from actual defensive work."""

    MOBILE_EVADE = "MOBILE_EVADE"
    MEDICAL_ANCHOR = "MEDICAL_ANCHOR"
    COMBAT_ANCHOR = "COMBAT_ANCHOR"


@dataclass(frozen=True)
class OrbitGeometry:
    """The locally assigned square-orbit geometry, never a fixed defensive ring."""

    r_vanguard: int
    r_ranger_inner: int
    r_ranger_outer: int
    r_sensor_outer: int
    gap: int
    r_commit: int
    r_screen: int
    lane_by_unit: dict[str, tuple[int, int]]


@dataclass(frozen=True)
class ThreatContact:
    enemy: UnitView
    tier: str
    square_radius: int
    core_eta: int
    next_layer_eta: int
    sector: str


@dataclass(frozen=True)
class Vacancy:
    ranger_id: UUID
    sector: str
    t_home: int
    t_queue: int
    t_heal: int
    t_return: int
    t_medical_gap: int
    fire_position: Position


@dataclass(frozen=True)
class ReliefAssignment:
    ranger_id: UUID
    vacancy_ranger_id: UUID
    t_relief: int
    fire_position: Position


@dataclass(frozen=True)
class FunnelPlan:
    gate_cell: Position | None = None
    block_cells: tuple[Position, ...] = ()
    assignments: tuple[tuple[UUID, Position], ...] = ()
    shortfall: int = 0


@dataclass(frozen=True)
class ShotIntent:
    ranger_id: UUID
    target_id: UUID
    expected_cell: Position
    predicted: bool


@dataclass
class ShotLedger:
    """Per-Tick expected damage accounting shared by every Ranger."""

    assigned_damage: Counter[UUID] = field(default_factory=Counter)
    intents: list[ShotIntent] = field(default_factory=list)

    def can_assign(self, enemy: UnitView | CoreView, *, predicted: bool) -> bool:
        # A stationary visible target has an exact HP budget.  A moving target gets
        # one backup shot because the simultaneous move prediction is uncertain.
        budget = _effective_hp(enemy) + (1 if predicted else 0)
        return self.assigned_damage[enemy.id] < budget

    def assign(self, ranger: Ranger, enemy: UnitView | CoreView, cell: Position) -> None:
        self.assigned_damage[enemy.id] += 1
        self.intents.append(
            ShotIntent(ranger.id, enemy.id, cell, cell != enemy.position)
        )


@dataclass(frozen=True)
class StrategicStandoff:
    """泛化战略相持:游侠互瞄死锁 / 被围残血先锋 / 被堵逃命工人。

    任一检出即触发 45°支援游侠从对角最远位换血(用户原则:主动触发、低限制、常用)。
    original_cell = 敌当前格 = 支援游侠要瞄的格(相持时双方都在预瞄对方下一步,原位恰好无人瞄)。
    """

    enemy: UnitView
    kind: str  # "ranger_ranger" | "vanguard_cornered" | "worker_fleeing"
    original_cell: Position


@dataclass(frozen=True)
class RangerAssaultPlan:
    """四游侠接触编组：两人预瞄、两人共用主动突击位。"""

    target: UnitView
    fire_cell: Position
    preaim_ids: tuple[UUID, ...]
    assault_ids: tuple[UUID, ...]



@dataclass
class LightningPlan:
    geometry: OrbitGeometry
    threats: tuple[ThreatContact, ...]
    vacancies: tuple[Vacancy, ...]
    reliefs: tuple[ReliefAssignment, ...]
    funnel: FunnelPlan
    anchor: CoreAnchorState
    committed_vanguards: set[UUID] = field(default_factory=set)


@dataclass(frozen=True)
class WorkerGoal:
    kind: str
    position: Position
    created_tick: int


@dataclass(frozen=True)
class EnemySighting:
    position: Position
    seen_tick: int
    is_core: bool
    unit_type: str | None = None  # "WORKER"/"VANGUARD"/"RANGER"/"CORE"，用于击杀记录分兵种


@dataclass(frozen=True)
class PlannedMove:
    destination: Position
    tick: int


@dataclass(frozen=True)
class PlannedRoute:
    object_id: str
    object_type: str
    start: Position
    goal: Position | None
    path: tuple[Position, ...]
    reason: str
    complete: bool


@dataclass(frozen=True)
class UnitLabel:
    object_type: str
    number: int


@dataclass(frozen=True)
class OverlayUnit:
    object_id: str
    object_type: str
    number: int
    position: Position


@dataclass(frozen=True)
class DecisionSummary:
    tick: int
    unit_actions: int
    has_core_action: bool
    previous_events: dict[str, int]
    resources: int
    resource_capacity: int
    population: int
    visible_enemies: int
    decisions: tuple[str, ...]
    # Dispatch is intentionally fixed: expose it to callers without restoring
    # the retired runtime mode switch.
    mode: str = MODE_LIGHTNING


@dataclass
class TacticMemory:
    known_obstacles: set[Position] = field(default_factory=set)
    resource_last_seen: dict[Position, int] = field(default_factory=dict)
    recovery_targets: list[Position] = field(default_factory=list)
    recovery_checked: set[Position] = field(default_factory=set)
    visited: Counter[Position] = field(default_factory=Counter)
    temporary_blocks: dict[Position, int] = field(default_factory=dict)
    worker_goals: dict[str, WorkerGoal] = field(default_factory=dict)
    worker_search_radius: dict[str, int] = field(default_factory=dict)
    enemy_sightings: dict[str, EnemySighting] = field(default_factory=dict)
    planned_moves: dict[str, PlannedMove] = field(default_factory=dict)
    event_totals: Counter[str] = field(default_factory=Counter)
    decision_totals: Counter[str] = field(default_factory=Counter)
    chunk_harvests: Counter[Chunk] = field(default_factory=Counter)
    chunk_next_refill: dict[Chunk, int] = field(default_factory=dict)
    chunk_anchors: dict[Chunk, Position] = field(default_factory=dict)
    chunk_last_probe: dict[Chunk, int] = field(default_factory=dict)
    unit_labels: dict[str, UnitLabel] = field(default_factory=dict)
    unit_label_counters: Counter[str] = field(default_factory=Counter)
    core_heading: Direction | None = None
    last_core_move_tick: int = 0
    last_core_damaged_tick: int = 0
    last_core_destroyed_tick: int = 0
    last_core_respawn_tick: int = 0
    core_shelter_target: Position | None = None
    core_shelter_entrance: Position | None = None
    unit_label_mapping: dict[str, str] = field(default_factory=dict)
    last_events: list[dict] = field(default_factory=list)
    unit_positions_for_overlay: dict[str, Position] = field(default_factory=dict)
    last_tick: int = 0
    # 哈雷彗星：派出小队追踪信标或打击自定义坐标的持续任务。
    comet_active: bool = False
    comet_mode: str = "beacon"  # "beacon" 追踪信标 | "coordinate" 自定义坐标
    comet_target: Position | None = None
    comet_vanguards: int = 3
    comet_rangers: int = 3
    comet_min_reserve_vanguards: int = 3
    comet_min_reserve_rangers: int = 3
    comet_wounded_threshold: float = 0.5
    comet_member_ids: set[str] = field(default_factory=set)
    comet_retreating_ids: set[str] = field(default_factory=set)
    comet_dispatched_tick: int = 0
    # 集合（Rally）：仅首次出发编队触发集合；替补成员不受影响。
    # comet_rally_enabled=False 时直接奔目标；True 时首批成员先到集合点近旁占位警戒，
    # 等齐队友再一起推进。集合点 = 沿目标方向距目标 comet_rally_distance 的格子，
    # 该格可能是障碍物，"到达集合点"判定为"集合点周围近邻可占格"。
    comet_rally_enabled: bool = False
    comet_rally_distance: int = 0  # 集合点距目标点的距离；0 表示贴目标点
    comet_rally_ready_ids: set[str] = field(default_factory=set)  # 已到集合点近旁的首批成员
    # 集合阶段状态：False=未开始(尚未进入集合)，True=集合已完成(首批已到齐并推进)。
    # 区分"空 rally_ready_ids"的两种含义：未开始 vs 已完成。开启 rally 且首次出发
    # 时进入"集合中"——靠 rally_ready_ids 仍有待到成员来识别；全员到齐后置 True，
    # 后续替补成员直接奔目标。
    comet_rally_done: bool = False
    # 闪电模式状态：当前巡逻点、轮转相位、每单位 claim 的敌方 Core、扇区分配。
    lightning_patrol_waypoint: tuple[int, int] | None = None
    lightning_patrol_phase: int = 0
    # === 网页控制台新增控制字段（load_control 写入，save/load 持久化）===
    # 恒星轨道半径 r：r>0 时 Core 沿半径 r 的方形周界（中心原点、边长 2r）四角轮转
    # 巡逻；r=0 未设置，Core 不巡逻。手动改 r 会触发迁移（见 core_orbit_migration_pending）。
    core_orbit_radius: int = 0
    # r 变更待迁移标志：load_control 检测到 r 改变时置 True，choose_actions 在拿到
    # turn.core.position 后据新 r 算出周界最近角并写入 core_target，然后清标志。
    # 不持久化（save/load 跳过），重启后丢失无妨——重启即重新读取 control 文件的 r。
    core_orbit_migration_pending: bool = False
    core_hold: bool = False  # 驻扎：Core 停在当前位置，行星照常巡逻
    core_target: tuple[int, int] | None = None  # 目标坐标，null = 无目标
    # 目标来源：user=用户前端设定（到达后 auto 驻扎 + 清 target）；
    # orbit=r 变更触发的轨道迁移（到达后清 target 并直接开始恒星巡逻，不停驻）。
    # 不持久化——重启后从 control 文件的 core_target 推断（有 target 即视为 user）。
    core_target_kind: str = "user"
    core_transfer_mode: str = "star"  # star|march|fortify
    # 退避三舍：敌方进入视野时，Core 在巡逻/转移途中主动绕开敌方方向（>0 半径才有
    # 巡逻；转移模式 star/march/fortify 也叠加生效）。敌方视野消失即停止绕避，恢复
    # 沿恒星轨道巡逻。优先级低于驻扎(core_hold)：驻扎时 Core 不动。
    core_evade_enemies: bool = False
    # 趁胜追击：敌方进入视野时，Core 在巡逻/转移途中主动朝敌方方向推进。敌方视野
    # 消失即停止追击，恢复巡逻。与退避三舍互斥（同时开时退避优先）。
    core_chase_enemies: bool = False
    # 御驾亲征：开启后 core_target 每 tick 设为 turn.beacon.position（动态跟随信标），
    # core_target_kind="beacon"。到达后不停驻（信标会动），持续跟随。复用彗星 beacon
    # 逻辑的目标解析思路。优先级低于驻扎，与退避/追击叠加（信标转移途中遇敌仍按退避/
    # 追击偏置）。关闭时清 target 回落恒星巡逻。
    core_pursue_beacon: bool = False
    build_queue: list[str] = field(default_factory=list)  # 造兵预定队列（顺序=优先级）
    # 开局引导：前 3 个造的兵固定工人（保证起手经济），之后完全由
    # 比例/阈值/上限/队列决定。计数已造的引导工人数，达 3 即关闭。
    bootstrap_workers_built: int = 0
    spawn_ratio: dict[str, int] = field(
        default_factory=lambda: {"ranger": 1, "vanguard": 1, "worker": 3}
    )  # 默认产兵比例（游侠:先锋:工人，无队列时按此比例趋近）；某兵种=0 表示停造该兵种；
    # 三者全 0 表示停止造兵囤资源。
    unit_caps: dict[str, int] = field(
        default_factory=lambda: {"worker": 20, "vanguard": 0, "ranger": 0}
    )  # 各兵种独立上限，0 = 无上限（默认仅工人上限 20）
    # 主动补兵阈值：某兵种当前数 < 阈值时优先补该兵种。0 = 不主动补该兵种。
    replenish_threshold: dict[str, int] = field(
        default_factory=lambda: {"ranger": 0, "vanguard": 0, "worker": 0}
    )
    # 补兵优先级：多兵种同时低于阈值时按此列表顺序补。默认 游侠 > 工人 > 先锋。
    replenish_priority: list[str] = field(
        default_factory=lambda: ["ranger", "worker", "vanguard"]
    )
    wartime_reserve: int = 150  # Core 战备资源存底（原硬编码 CORE_WARTIME_RESOURCE_FLOOR）
    # 先锋敌方 Core 猎杀链已废弃:lightning_claims/lightning_blacklist 字段随
    # _lightning_acquire_target 等函数一并删除。先锋只守近轨,不再 claim 敌方 Core。
    lightning_sectors: dict[str, tuple[int, int]] = field(default_factory=dict)
    # 绕 Core 转的行星轨道点位环：每单位相对自己 anchor 的推进步数 offset(0..M-1)，
    # 到点死区后推进。圆心是 core.position；M=next_pow2(层内单位数) 的动态点位网格。
    lightning_orbit_phase: dict[str, int] = field(default_factory=dict)
    # 点位环 anchor 缓存：uid → bit-reversal(group_index) 得到的角/中点序号。
    # offset 只在 anchor 不变时有效；旧系统(4 角 phase 0..3)迁移或网格重排导致
    # anchor 变化时，_lightning_orbit_waypoint 会据此把 offset 重置回 0（到自己的
    # anchor 重新锚定），避免存量单位起点撞车、同一点位扎堆。
    lightning_orbit_anchor: dict[str, int] = field(default_factory=dict)
    # 已废弃(开路轨道删除后运行时无人读写):仅保留 save/load 以兼容旧 memory 文件。
    lightning_breakthrough_phase: dict[str, int] = field(default_factory=dict)
    # 行星轨道 lane 分配缓存：role(str) → UUID → (radius, group_idx)。
    # RANGER/WORKER 的 lanes 由 _lightning_assign_shared_middle_lanes 从统一队列派生
    # (游侠占内层、工人接外层,共享同一组同心半径);VANGUARD 独立(近行星)。
    lightning_orbit_lanes: dict[str, dict[str, tuple[int, int]]] = field(default_factory=dict)
    # 游侠+工人共享中轨的全局有序队列:uid → 序号(0 起,内→外)。游侠段 [0,rk),
    # 工人段 [rk,total)。新游侠出生→游侠段扩 1→挤出最靠内的工人→该工人落到队尾。
    # 总数(rk+wk)不变时不重算,保证位置稳定不抖动。
    lightning_shared_orbit_seq: dict[str, int] = field(default_factory=dict)
    # 上一 tick 存活单位 {uid: unit_type 名},用于"阵亡补同种"判定:对比当前存活,
    # 差集即本 tick 阵亡的单位及其兵种,据此决定 pop≥9 时补造哪一类兵。
    lightning_last_alive_uids: dict[str, str] = field(default_factory=dict)
    # 本 tick 阵亡单位 {uid: unit_type 名},由 observe 每 tick 重算(运行时,
    # 不序列化)。_select_spawn 只读它,保证被多次调用(预检 + 真正决策)时结果一致。
    lightning_recent_deaths: dict[str, str] = field(default_factory=dict, repr=False)
    # 鬼打墙逃生：UUID → 连续"小范围震荡"检测计数（达阈值触发逃生）。
    lightning_unit_stuck_counters: dict[str, int] = field(default_factory=dict)
    # 鬼打墙逃生：UUID → 逃生模式截止 tick。逃生期间忽略巡逻目标，
    # 只往"开阔 + 低 visited 密度"方向走，强制脱出障碍死角。
    lightning_unit_escape_until: dict[str, int] = field(default_factory=dict)
    attacked_units: dict[str, int] = field(default_factory=dict)
    replacement_queue: Counter[str] = field(default_factory=Counter)
    # 进攻模式医疗轮转：patient_id → healer rotation index
    aggress_heal_rotations: dict[str, int] = field(default_factory=dict)
    control_mtime: int = 0
    total_resources_harvested: int = 0
    total_resources_deposited: int = 0
    total_resources_captured: int = 0
    enemy_cores_destroyed: int = 0
    # 敌核击杀去重：记录已计入击杀的敌方 Core id，避免同一敌核多次计数。
    battle_enemy_cores_seen: set[str] = field(default_factory=set)
    # 敌方单位击杀去重：记录已计入击杀的敌方单位 id，避免同一单位在
    # UNIT_DAMAGED(hp=0) 与 DESTRUCTION_PARTICIPATION(UNIT) 两条事件中双计。
    battle_enemy_units_seen: set[str] = field(default_factory=set)
    # 每 tick 瞬态快照：observe 清理 enemy_sightings 之前冻结的 {enemy_id: unit_type}，
    # 供 _append_battle_history 在敌方单位阵亡（已从 visible_enemies 消失）时查兵种。
    # 不持久化（observe 每 tick 重建）。
    enemy_type_snapshot: dict[str, str] = field(default_factory=dict, repr=False)
    first_observed_tick: int = 0
    observed_turns: int = 0
    units_lost: int = 0
    current_tick_interval: int = field(default=0, repr=False)
    current_routes: dict[str, PlannedRoute] = field(default_factory=dict, repr=False)
    current_units: dict[str, OverlayUnit] = field(default_factory=dict, repr=False)
    current_resource_cells: set[Position] = field(default_factory=set, repr=False)
    browser_resource_hints: set[Position] = field(default_factory=set, repr=False)
    browser_intel_captured_at: str | None = field(default=None, repr=False)
    browser_intel_age_seconds: int = field(default=0, repr=False)
    browser_intel_online: bool = field(default=False, repr=False)
    observations: list[str] = field(default_factory=list, repr=False)
    unit_positions: dict[str, Position] = field(default_factory=dict, repr=False)
    last_position_tick: dict[str, int] = field(default_factory=dict, repr=False)
    # 每单位最近一次移动方向(惯性用,防绕圈掉头横跳)。运行时维护,repr=False。
    unit_headings: dict[str, Direction] = field(default_factory=dict, repr=False)
    recent_positions: dict[str, list[Position]] = field(default_factory=dict, repr=False)
    enemy_positions: dict[str, Position] = field(default_factory=dict, repr=False)
    enemy_prev: dict[str, Position] = field(default_factory=dict, repr=False)
    # 敌方轨迹库：每个敌方单位最近 7 帧的位置序列（用于识别 ZIGZAG/LINEAR/CIRCLE
    # 运动规律，替代单帧速度外推）。不持久化——session 内瞬态。
    enemy_trails: dict[str, list[Position]] = field(default_factory=dict, repr=False)
    ENEMY_TRAIL_WINDOW = 7
    shot_miss_counts: Counter[str] = field(default_factory=Counter, repr=False)
    shot_miss_ticks: dict[str, int] = field(default_factory=dict, repr=False)
    # 按轴脱靶聚合：key = f"{target_id}|{x|y}"，射击时按 expected_cell 相对敌人
    # 当前格的主轴记录。ZIGZAG 围猎时，连续在同一轴脱靶会惩罚该轴、导向对轴，
    # 破解"永远瞄错方向"的僵持。命中即清零该目标全部轴计数。
    axis_miss_counts: Counter[str] = field(default_factory=Counter, repr=False)
    axis_miss_ticks: dict[str, int] = field(default_factory=dict, repr=False)
    current_shot_cells: set[tuple[str, Position]] = field(
        default_factory=set,
        repr=False,
    )
    # 游侠单杀先锋舞步状态机: key=f"{ranger_id}|{enemy_vanguard_id}" → phase dict。
    # 瞬态、不持久化(session 内),repr=False。load/save 不处理(同 enemy_trails 先例)。
    # phase: "APPROACH_GAP"|"ADJACENT_BACK"|"REAIM_GAP_HP2"|"FLEE_AMBUSH"。
    vanguard_dance_phase: dict[str, dict] = field(default_factory=dict, repr=False)
    # 本 tick 45°支援指派的游侠 id(瞬态,每 tick choose_actions 开头清空)。
    # 防同一相持一 tick 挑两名支援游侠。
    standoff_support_assigned: set[str] = field(default_factory=set, repr=False)
    # per-ranger 连续未开火 tick 数(支援指派但本格无合法射击线时 +1,开火即清零)。
    standoff_support_stall: dict[str, int] = field(default_factory=dict)
    # per-ranger 冷却到期 tick(冷却期内不再被指派为支援游侠,回落常规巡逻)。
    standoff_support_cooldown: dict[str, int] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> TacticMemory:
        if not path.is_file():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return cls()
            if data.get("version") != 2:
                return cls()
            memory = cls()
            memory.known_obstacles = {
                (int(position[0]), int(position[1]))
                for position in data.get("known_obstacles", ())
            }
            memory.resource_last_seen = {
                (int(x), int(y)): int(tick)
                for x, y, tick in data.get("resource_last_seen", ())
            }
            memory.recovery_targets = [
                (int(position[0]), int(position[1]))
                for position in data.get("recovery_targets", ())
            ]
            memory.recovery_checked = {
                (int(position[0]), int(position[1]))
                for position in data.get("recovery_checked", ())
            }
            recovery_hints = _load_recovery_target_hints(
                path.with_name(RECOVERY_TARGETS_FILENAME)
            )
            for position in recovery_hints or ():
                if (
                    position not in memory.recovery_checked
                    and position not in memory.recovery_targets
                ):
                    memory.recovery_targets.append(position)
            memory.visited = Counter(
                {
                    (int(x), int(y)): int(count)
                    for x, y, count in data.get("visited", ())
                }
            )
            memory.temporary_blocks = {
                (int(x), int(y)): int(until)
                for x, y, until in data.get("temporary_blocks", ())
            }
            memory.worker_goals = {
                unit_id: WorkerGoal(
                    kind=value[0],
                    position=(int(value[1]), int(value[2])),
                    created_tick=int(value[3]),
                )
                for unit_id, value in data.get("worker_goals", {}).items()
            }
            memory.worker_search_radius = {
                str(unit_id): max(0, int(radius))
                for unit_id, radius in data.get("worker_search_radius", {}).items()
            }
            memory.enemy_sightings = {
                object_id: EnemySighting(
                    position=(int(value[0]), int(value[1])),
                    seen_tick=int(value[2]),
                    is_core=bool(value[3]),
                    unit_type=(
                        str(value[4]) if len(value) >= 5 and value[4] else None
                    ),
                )
                for object_id, value in data.get("enemy_sightings", {}).items()
                if isinstance(value, list) and len(value) >= 4
            }
            memory.planned_moves = {
                unit_id: PlannedMove(
                    destination=(int(value[0]), int(value[1])),
                    tick=int(value[2]),
                )
                for unit_id, value in data.get("planned_moves", {}).items()
            }
            memory.event_totals = Counter(data.get("event_totals", {}))
            memory.decision_totals = Counter(data.get("decision_totals", {}))
            memory.chunk_harvests = Counter(
                {
                    (int(cx), int(cy)): int(count)
                    for cx, cy, count in data.get("chunk_harvests", ())
                }
            )
            memory.chunk_next_refill = {
                (int(cx), int(cy)): int(tick)
                for cx, cy, tick in data.get("chunk_next_refill", ())
            }
            memory.chunk_anchors = {
                (int(cx), int(cy)): (int(x), int(y))
                for cx, cy, x, y in data.get("chunk_anchors", ())
            }
            memory.chunk_last_probe = {
                (int(cx), int(cy)): int(tick)
                for cx, cy, tick in data.get("chunk_last_probe", ())
            }
            memory.unit_labels = {
                unit_id: UnitLabel(object_type=str(value[0]), number=int(value[1]))
                for unit_id, value in data.get("unit_labels", {}).items()
            }
            memory.unit_label_counters = Counter(
                {
                    str(object_type): int(number)
                    for object_type, number in data.get(
                        "unit_label_counters",
                        {},
                    ).items()
                }
            )
            heading = data.get("core_heading")
            memory.core_heading = Direction(heading) if heading is not None else None
            memory.last_core_move_tick = int(data.get("last_core_move_tick", 0))
            memory.last_core_damaged_tick = int(data.get("last_core_damaged_tick", 0))
            memory.last_core_destroyed_tick = int(
                data.get("last_core_destroyed_tick", 0)
            )
            memory.last_core_respawn_tick = int(data.get("last_core_respawn_tick", 0))
            shelter_target = data.get("core_shelter_target")
            if isinstance(shelter_target, list) and len(shelter_target) == 2:
                memory.core_shelter_target = (
                    int(shelter_target[0]),
                    int(shelter_target[1]),
                )
            shelter_entrance = data.get("core_shelter_entrance")
            if isinstance(shelter_entrance, list) and len(shelter_entrance) == 2:
                memory.core_shelter_entrance = (
                    int(shelter_entrance[0]),
                    int(shelter_entrance[1]),
                )
            memory.last_tick = int(data.get("last_tick", 0))
            memory.comet_active = bool(data.get("comet_active", False))
            memory.comet_mode = str(data.get("comet_mode", "beacon")) or "beacon"
            if memory.comet_mode not in ("beacon", "coordinate"):
                memory.comet_mode = "beacon"
            comet_target = data.get("comet_target")
            if isinstance(comet_target, list) and len(comet_target) == 2:
                memory.comet_target = (int(comet_target[0]), int(comet_target[1]))
            elif memory.comet_mode == "coordinate":
                memory.comet_target = None
            memory.comet_vanguards = max(
                0, int(data.get("comet_vanguards", memory.comet_vanguards))
            )
            memory.comet_rangers = max(
                0, int(data.get("comet_rangers", memory.comet_rangers))
            )
            memory.comet_min_reserve_vanguards = max(
                0,
                int(data.get(
                    "comet_min_reserve_vanguards",
                    memory.comet_min_reserve_vanguards,
                )),
            )
            memory.comet_min_reserve_rangers = max(
                0,
                int(data.get(
                    "comet_min_reserve_rangers",
                    memory.comet_min_reserve_rangers,
                )),
            )
            memory.comet_wounded_threshold = max(
                0.0,
                min(1.0, float(data.get("comet_wounded_threshold", 0.5))),
            )
            memory.comet_member_ids = {
                str(unit_id)
                for unit_id in data.get("comet_member_ids", ())
                if unit_id
            }
            memory.comet_retreating_ids = {
                str(unit_id)
                for unit_id in data.get("comet_retreating_ids", ())
                if unit_id
            }
            memory.comet_dispatched_tick = max(
                0, int(data.get("comet_dispatched_tick", 0))
            )
            memory.comet_rally_enabled = bool(
                data.get("comet_rally_enabled", False)
            )
            memory.comet_rally_distance = max(
                0, int(data.get("comet_rally_distance", 0))
            )
            memory.comet_rally_ready_ids = {
                str(unit_id)
                for unit_id in data.get("comet_rally_ready_ids", ())
                if unit_id
            }
            memory.comet_rally_done = bool(data.get("comet_rally_done", False))
            memory.replacement_queue = Counter(
                {
                    str(unit_type): max(0, int(count))
                    for unit_type, count in data.get(
                        "replacement_queue",
                        {},
                    ).items()
                    if int(count) > 0
                }
            )
            memory.total_resources_harvested = int(
                data.get("total_resources_harvested", 0)
            )
            memory.total_resources_deposited = int(
                data.get("total_resources_deposited", 0)
            )
            memory.total_resources_captured = int(
                data.get("total_resources_captured", 0)
            )
            memory.enemy_cores_destroyed = int(data.get("enemy_cores_destroyed", 0))
            memory.battle_enemy_cores_seen = {
                str(core_id) for core_id in data.get("battle_enemy_cores_seen", ())
            }
            memory.battle_enemy_units_seen = {
                str(unit_id) for unit_id in data.get("battle_enemy_units_seen", ())
            }
            memory.first_observed_tick = int(data.get("first_observed_tick", 0))
            memory.observed_turns = int(data.get("observed_turns", 0))
            memory.units_lost = int(data.get("units_lost", 0))
            raw_waypoint = data.get("lightning_patrol_waypoint")
            if isinstance(raw_waypoint, list) and len(raw_waypoint) == 2:
                memory.lightning_patrol_waypoint = (
                    int(raw_waypoint[0]),
                    int(raw_waypoint[1]),
                )
            memory.lightning_patrol_phase = int(
                data.get("lightning_patrol_phase", 0)
            )
            # 网页控制台控制字段反序列化
            memory.core_orbit_radius = max(
                0,
                int(data.get("core_orbit_radius", 0)),
            )
            memory.core_hold = bool(data.get("core_hold", False))
            raw_core_target = data.get("core_target")
            if isinstance(raw_core_target, list) and len(raw_core_target) == 2:
                memory.core_target = (
                    int(raw_core_target[0]),
                    int(raw_core_target[1]),
                )
                # 用户从控制文件设的目标 → 到达后应 auto 驻扎。
                memory.core_target_kind = "user"
            raw_transfer_mode = data.get("core_transfer_mode", "star")
            if raw_transfer_mode in TRANSFER_MODES:
                memory.core_transfer_mode = raw_transfer_mode
            memory.core_evade_enemies = bool(data.get("core_evade_enemies", False))
            memory.core_chase_enemies = bool(data.get("core_chase_enemies", False))
            memory.core_pursue_beacon = bool(data.get("core_pursue_beacon", False))
            raw_queue = data.get("build_queue", ())
            memory.build_queue = [
                str(item)
                for item in raw_queue
                if isinstance(item, str) and item in CONTROL_UNIT_TYPES
            ][:MAX_BUILD_QUEUE_LENGTH]
            # 开局引导计数：重启后保留进度，避免重新固定造 3 工人。
            memory.bootstrap_workers_built = max(
                0, min(3, int(data.get("bootstrap_workers_built", 0)))
            )
            raw_ratio = data.get("spawn_ratio", {})
            if isinstance(raw_ratio, dict):
                # 比例允许全 0（停止造兵囤资源）；某兵种=0 停造该兵种。
                # 各 key 缺省即用默认比例 1:1:3（游侠:先锋:工人）。
                ratio: dict[str, int] = {}
                for key, default in (
                    ("ranger", 1),
                    ("vanguard", 1),
                    ("worker", 3),
                ):
                    raw_value = raw_ratio.get(key, default)
                    if (
                        isinstance(raw_value, int)
                        and not isinstance(raw_value, bool)
                        and raw_value >= 0
                    ):
                        ratio[key] = raw_value
                    else:
                        ratio[key] = default
                memory.spawn_ratio = {
                    "ranger": ratio["ranger"],
                    "vanguard": ratio["vanguard"],
                    "worker": ratio["worker"],
                }
            raw_caps = data.get("unit_caps", {})
            if isinstance(raw_caps, dict):
                caps = {
                    key: max(0, int(raw_caps.get(key, 0)))
                    for key in ("worker", "vanguard", "ranger")
                }
                memory.unit_caps = caps
            # 补兵阈值：各兵种 < 阈值时优先补。0 = 不主动补该兵种。
            raw_threshold = data.get("replenish_threshold", {})
            if isinstance(raw_threshold, dict):
                memory.replenish_threshold = {
                    key: max(0, int(raw_threshold.get(key, 0)))
                    for key in ("ranger", "vanguard", "worker")
                }
            # 补兵优先级：多兵种同时低于阈值时按此顺序补。
            raw_priority = data.get("replenish_priority")
            if isinstance(raw_priority, list):
                priority = [
                    str(item).lower()
                    for item in raw_priority
                    if isinstance(item, str)
                    and item.lower() in ("ranger", "vanguard", "worker")
                ]
                # 缺失的兵种补到末尾，保证列表始终覆盖三类。
                for key in ("ranger", "worker", "vanguard"):
                    if key not in priority:
                        priority.append(key)
                memory.replenish_priority = priority
            memory.wartime_reserve = max(
                0,
                int(data.get("wartime_reserve", CORE_WARTIME_RESOURCE_FLOOR)),
            )
            raw_sectors = data.get("lightning_sectors", {})
            memory.lightning_sectors = {
                str(unit_id): (int(sector[0]), int(sector[1]))
                for unit_id, sector in raw_sectors.items()
                if isinstance(sector, list) and len(sector) == 2
            }
            memory.lightning_orbit_phase = {
                str(unit_id): int(phase)
                for unit_id, phase in data.get("lightning_orbit_phase", {}).items()
            }
            memory.lightning_orbit_anchor = {
                str(unit_id): int(anchor)
                for unit_id, anchor in data.get("lightning_orbit_anchor", {}).items()
            }
            memory.lightning_breakthrough_phase = {
                str(unit_id): int(phase) % 4
                for unit_id, phase in data.get("lightning_breakthrough_phase", {}).items()
            }
            raw_orbit_lanes = data.get("lightning_orbit_lanes", {})
            memory.lightning_orbit_lanes = {
                str(role): {
                    str(uid): tuple(int(v) for v in value)
                    if isinstance(value, (list, tuple))
                    else (int(value), 0)
                    for uid, value in lanes.items()
                }
                for role, lanes in raw_orbit_lanes.items()
                if isinstance(lanes, dict)
            }
            memory.lightning_shared_orbit_seq = {
                str(uid): int(seq)
                for uid, seq in data.get("lightning_shared_orbit_seq", {}).items()
            }
            memory.lightning_last_alive_uids = {
                str(uid): str(t)
                for uid, t in data.get("lightning_last_alive_uids", {}).items()
            }
            memory.lightning_unit_stuck_counters = {
                str(unit_id): int(count)
                for unit_id, count in data.get(
                    "lightning_unit_stuck_counters", {}
                ).items()
            }
            memory.lightning_unit_escape_until = {
                str(unit_id): int(until)
                for unit_id, until in data.get(
                    "lightning_unit_escape_until", {}
                ).items()
            }
            memory.standoff_support_stall = {
                str(uid): int(v)
                for uid, v in data.get("standoff_support_stall", {}).items()
            }
            memory.standoff_support_cooldown = {
                str(uid): int(v)
                for uid, v in data.get("standoff_support_cooldown", {}).items()
            }
            return memory
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return cls()

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 2,
            "known_obstacles": [list(position) for position in sorted(self.known_obstacles)],
            "resource_last_seen": [
                [position[0], position[1], tick]
                for position, tick in sorted(self.resource_last_seen.items())
            ],
            "recovery_targets": [list(position) for position in self.recovery_targets],
            "recovery_checked": [
                list(position) for position in sorted(self.recovery_checked)
            ],
            "visited": [
                [position[0], position[1], count]
                for position, count in sorted(self.visited.items())
            ],
            "temporary_blocks": [
                [position[0], position[1], until]
                for position, until in sorted(self.temporary_blocks.items())
            ],
            "worker_goals": {
                unit_id: [goal.kind, goal.position[0], goal.position[1], goal.created_tick]
                for unit_id, goal in sorted(self.worker_goals.items())
            },
            "worker_search_radius": dict(sorted(self.worker_search_radius.items())),
            "enemy_sightings": {
                object_id: [
                    sighting.position[0],
                    sighting.position[1],
                    sighting.seen_tick,
                    sighting.is_core,
                    sighting.unit_type,
                ]
                for object_id, sighting in sorted(self.enemy_sightings.items())
            },
            "planned_moves": {
                unit_id: [move.destination[0], move.destination[1], move.tick]
                for unit_id, move in sorted(self.planned_moves.items())
            },
            "event_totals": dict(sorted(self.event_totals.items())),
            "decision_totals": dict(sorted(self.decision_totals.items())),
            "chunk_harvests": [
                [chunk[0], chunk[1], count]
                for chunk, count in sorted(self.chunk_harvests.items())
            ],
            "chunk_next_refill": [
                [chunk[0], chunk[1], tick]
                for chunk, tick in sorted(self.chunk_next_refill.items())
            ],
            "chunk_anchors": [
                [chunk[0], chunk[1], position[0], position[1]]
                for chunk, position in sorted(self.chunk_anchors.items())
            ],
            "chunk_last_probe": [
                [chunk[0], chunk[1], tick]
                for chunk, tick in sorted(self.chunk_last_probe.items())
            ],
            "unit_labels": {
                unit_id: [label.object_type, label.number]
                for unit_id, label in sorted(self.unit_labels.items())
            },
            "unit_label_counters": dict(sorted(self.unit_label_counters.items())),
            "core_heading": (
                self.core_heading.value if self.core_heading is not None else None
            ),
            "last_core_move_tick": self.last_core_move_tick,
            "last_core_damaged_tick": self.last_core_damaged_tick,
            "last_core_destroyed_tick": self.last_core_destroyed_tick,
            "last_core_respawn_tick": self.last_core_respawn_tick,
            "core_shelter_target": (
                list(self.core_shelter_target)
                if self.core_shelter_target is not None
                else None
            ),
            "core_shelter_entrance": (
                list(self.core_shelter_entrance)
                if self.core_shelter_entrance is not None
                else None
            ),
            "last_tick": self.last_tick,
            "comet_active": self.comet_active,
            "comet_mode": self.comet_mode,
            "comet_target": (
                list(self.comet_target)
                if self.comet_target is not None
                else None
            ),
            "comet_vanguards": self.comet_vanguards,
            "comet_rangers": self.comet_rangers,
            "comet_min_reserve_vanguards": self.comet_min_reserve_vanguards,
            "comet_min_reserve_rangers": self.comet_min_reserve_rangers,
            "comet_wounded_threshold": self.comet_wounded_threshold,
            "comet_member_ids": sorted(self.comet_member_ids),
            "comet_retreating_ids": sorted(self.comet_retreating_ids),
            "comet_dispatched_tick": self.comet_dispatched_tick,
            "comet_rally_enabled": self.comet_rally_enabled,
            "comet_rally_distance": self.comet_rally_distance,
            "comet_rally_ready_ids": sorted(self.comet_rally_ready_ids),
            "comet_rally_done": self.comet_rally_done,
            "replacement_queue": dict(sorted(self.replacement_queue.items())),
            "total_resources_harvested": self.total_resources_harvested,
            "total_resources_deposited": self.total_resources_deposited,
            "total_resources_captured": self.total_resources_captured,
            "enemy_cores_destroyed": self.enemy_cores_destroyed,
            "battle_enemy_cores_seen": sorted(self.battle_enemy_cores_seen),
            "battle_enemy_units_seen": sorted(self.battle_enemy_units_seen),
            "first_observed_tick": self.first_observed_tick,
            "observed_turns": self.observed_turns,
            "units_lost": self.units_lost,
            "lightning_patrol_waypoint": (
                [
                    self.lightning_patrol_waypoint[0],
                    self.lightning_patrol_waypoint[1],
                ]
                if self.lightning_patrol_waypoint is not None
                else None
            ),
            "lightning_patrol_phase": self.lightning_patrol_phase,
            # 网页控制台控制字段持久化
            "core_orbit_radius": self.core_orbit_radius,
            "core_hold": self.core_hold,
            "core_target": (
                [self.core_target[0], self.core_target[1]]
                if self.core_target is not None
                else None
            ),
            "core_transfer_mode": self.core_transfer_mode,
            "core_evade_enemies": self.core_evade_enemies,
            "core_chase_enemies": self.core_chase_enemies,
            "core_pursue_beacon": self.core_pursue_beacon,
            "build_queue": list(self.build_queue),
            "bootstrap_workers_built": self.bootstrap_workers_built,
            "spawn_ratio": dict(self.spawn_ratio),
            "unit_caps": dict(self.unit_caps),
            "replenish_threshold": dict(self.replenish_threshold),
            "replenish_priority": list(self.replenish_priority),
            "wartime_reserve": self.wartime_reserve,
            "lightning_sectors": {
                unit_id: [sector[0], sector[1]]
                for unit_id, sector in sorted(self.lightning_sectors.items())
            },
            "lightning_orbit_phase": dict(sorted(self.lightning_orbit_phase.items())),
            "lightning_orbit_anchor": dict(sorted(self.lightning_orbit_anchor.items())),
            "lightning_breakthrough_phase": dict(
                sorted(self.lightning_breakthrough_phase.items())
            ),
            "lightning_orbit_lanes": {
                role: dict(sorted(lanes.items()))
                for role, lanes in sorted(self.lightning_orbit_lanes.items())
            },
            "lightning_shared_orbit_seq": dict(
                sorted(self.lightning_shared_orbit_seq.items())
            ),
            "lightning_last_alive_uids": dict(
                sorted(self.lightning_last_alive_uids.items())
            ),
            "lightning_unit_stuck_counters": dict(
                sorted(self.lightning_unit_stuck_counters.items())
            ),
            "lightning_unit_escape_until": dict(
                sorted(self.lightning_unit_escape_until.items())
            ),
            "standoff_support_stall": dict(
                sorted(self.standoff_support_stall.items())
            ),
            "standoff_support_cooldown": dict(
                sorted(self.standoff_support_cooldown.items())
            ),
        }
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        # 双 agent 竞争写同一文件时会 PermissionError（WinError 5）；重试 + 降级，绝不崩溃
        for attempt in range(4):
            try:
                temporary.replace(path)
                break
            except OSError:
                if attempt < 3:
                    time.sleep(0.2)
                else:
                    try:
                        path.write_text(
                            json.dumps(
                                payload, ensure_ascii=False, separators=(",", ":")
                            ),
                            encoding="utf-8",
                        )
                    except OSError:
                        # 保存失败不致命：下一 tick 会再试
                        pass
        try:
            self.save_routes(path.with_name(ROUTES_FILENAME))
        except OSError:
            # The overlay is observational only and must never stop live play.
            pass

    def save_routes(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        routes = [
            {
                "object_id": route.object_id,
                "object_type": route.object_type,
                "number": (
                    self.unit_labels[route.object_id].number
                    if route.object_id in self.unit_labels
                    else None
                ),
                "start": list(route.start),
                "goal": list(route.goal) if route.goal is not None else None,
                "path": [list(position) for position in route.path],
                "reason": route.reason,
                "complete": route.complete,
            }
            for route in sorted(
                self.current_routes.values(),
                key=lambda route: (route.object_type, route.object_id),
            )
        ]
        units = [
            {
                "object_id": unit.object_id,
                "object_type": unit.object_type,
                "number": unit.number,
                "position": list(unit.position),
            }
            for unit in sorted(
                self.current_units.values(),
                key=lambda unit: (unit.object_type, unit.number, unit.object_id),
            )
        ]
        payload = {
            "version": ROUTE_OVERLAY_VERSION,
            "tick": self.last_tick,
            "routes": routes,
            "units": units,
            "resources": [
                list(position) for position in sorted(self.current_resource_cells)
            ],
        }
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        temporary.replace(path)

    def observe(self, turn: Turn) -> None:
        previous_labels = dict(self.unit_labels)
        previous_unit_ids = set(previous_labels)
        self.current_tick_interval = (
            max(0, turn.tick - self.last_tick) if self.last_tick > 0 else 0
        )
        if self.first_observed_tick <= 0:
            self.first_observed_tick = turn.tick
        self.observed_turns += 1
        self.observations.clear()
        self.current_routes.clear()
        self.current_units.clear()
        self.current_resource_cells = set(turn.resource_cells)
        # 先冻结敌方单位→兵种映射，供 _append_battle_history 查阵亡敌方单位类型。
        # 必须在下面 enemy_sightings 清理之前做：阵亡敌方单位本 tick 从 visible_enemies
        # 消失，若其位置落入我方视野，observe 会清掉它的 sighting，导致历史记录无法
        # 判定兵种而误归 enemy_unknown。
        self.enemy_type_snapshot = {
            str(enemy.id): (
                "CORE"
                if isinstance(enemy, CoreView)
                else enemy.unit_type.value
            )
            for enemy in turn.visible_enemies
        }
        for enemy_id, sighting in self.enemy_sightings.items():
            self.enemy_type_snapshot.setdefault(enemy_id, sighting.unit_type)
        self.event_totals.update(event.event_type for event in turn.events)
        if (
            turn.core is not None
            and turn.core.view.state is CoreState.MOVING
            and turn.core.view.move_direction is not None
        ):
            self.core_heading = turn.core.view.move_direction
        live_unit_ids = {str(unit.id) for unit in turn.units}
        lost_unit_ids = previous_unit_ids - live_unit_ids
        self.units_lost += len(lost_unit_ids)
        # 阵亡补同种:本 tick 阵亡的单位及其兵种(用清理前的 unit_labels 查 type)。
        # _select_spawn 读 lightning_recent_deaths 决定 pop≥9 补哪类兵;同时把
        # 当前存活编制快照到 lightning_last_alive_uids 供下 tick 算差集。
        self.lightning_recent_deaths = {
            uid: previous_labels[uid].object_type
            for uid in lost_unit_ids
            if uid in previous_labels
        }
        self.lightning_last_alive_uids = {
            str(unit.id): unit.unit_type.name for unit in turn.units
        }
        self.replacement_queue.clear()
        self.unit_labels = {
            unit_id: label
            for unit_id, label in self.unit_labels.items()
            if unit_id in live_unit_ids
        }
        for label in self.unit_labels.values():
            self.unit_label_counters[label.object_type] = max(
                self.unit_label_counters[label.object_type],
                label.number,
            )
        for unit in sorted(
            turn.units,
            key=lambda candidate: (candidate.unit_type.value, candidate.id.bytes),
        ):
            unit_id = str(unit.id)
            object_type = unit.unit_type.value
            label = self.unit_labels.get(unit_id)
            if label is None or label.object_type != object_type:
                self.unit_label_counters[object_type] += 1
                label = UnitLabel(
                    object_type=object_type,
                    number=self.unit_label_counters[object_type],
                )
                self.unit_labels[unit_id] = label
            self.current_units[unit_id] = OverlayUnit(
                object_id=unit_id,
                object_type=object_type,
                number=label.number,
                position=unit.position,
            )
        live_worker_ids = {str(worker.id) for worker in turn.workers}
        self.worker_goals = {
            unit_id: goal
            for unit_id, goal in self.worker_goals.items()
            if unit_id in live_worker_ids
        }
        self.worker_search_radius = {
            unit_id: radius
            for unit_id, radius in self.worker_search_radius.items()
            if unit_id in live_worker_ids
        }

        for event in turn.events:
            if event.event_type == "CORE_DAMAGED":
                self.last_core_damaged_tick = turn.tick
                self.core_reinforcement_until_tick = max(
                    self.core_reinforcement_until_tick,
                    turn.tick + AGGRESS_CORE_REINFORCEMENT_HOLD_TICKS,
                )
            elif event.event_type == "CORE_DESTROYED":
                self.last_core_damaged_tick = turn.tick
                self.last_core_destroyed_tick = turn.tick
                self.clear_core_shelter_memory()
                self.core_heading = None
                self.last_core_move_tick = 0
                self.clear_comet_state()
                self.clear_local_core_sortie()
            elif event.event_type == "CORE_RESPAWNED":
                self.last_core_respawn_tick = turn.tick
                self.clear_core_shelter_memory()
                self.core_heading = None
                self.last_core_move_tick = 0
                self.clear_comet_state()
                self.clear_local_core_sortie()
            # 广播系统：单位被攻击 → 记录并通知其他单位支援
            if event.event_type == "UNIT_DAMAGED" and event.target_id is not None:
                target_key = str(event.target_id)
                if target_key in self.unit_labels:
                    self.attacked_units[target_key] = turn.tick
            # 记录战斗事件（供 overlay 快速定位）
            if event.event_type in {
                "SHOT_HIT",
                "SHOT_MISSED",
                "UNIT_DAMAGED",
                "DESTRUCTION_PARTICIPATION",
                "CORE_RESOURCES_CAPTURED",
                "SWEEP_RESOLVED",
                "UNIT_SELF_DESTRUCTED",
                "CORE_DESTROYED",
            }:
                self.last_events.append(
                    {
                        "tick": turn.tick,
                        "type": event.event_type,
                        "position": (
                            [event.position[0], event.position[1]]
                            if event.position is not None
                            else None
                        ),
                    }
                )
                if len(self.last_events) > 15:
                    self.last_events.pop(0)
            if (
                event.event_type == "SHOT_MISSED"
                and event.target_id is not None
                and event.position is not None
            ):
                shot_key = _shot_cell_key(event.target_id, event.position)
                self.shot_miss_counts[shot_key] += 1
                self.shot_miss_ticks[shot_key] = turn.tick
            elif event.event_type == "SHOT_HIT" and event.target_id is not None:
                target_prefix = f"{event.target_id}|"
                for shot_key in tuple(self.shot_miss_counts):
                    if shot_key.startswith(target_prefix):
                        self.shot_miss_counts.pop(shot_key, None)
                        self.shot_miss_ticks.pop(shot_key, None)
                # 命中即清零该目标的按轴脱靶计数（这一轴被验证有效）。
                for axis_key in tuple(self.axis_miss_counts):
                    if axis_key.startswith(target_prefix):
                        self.axis_miss_counts.pop(axis_key, None)
                        self.axis_miss_ticks.pop(axis_key, None)
            actor_key = str(event.actor_id) if event.actor_id is not None else None
            if event.event_type == "UNIT_MOVE_FAILED" and actor_key is not None:
                planned = self.planned_moves.pop(actor_key, None)
                if planned is not None and planned.tick == event.tick:
                    if event.reason_code == "MOVE_BLOCKED_TERRAIN":
                        self.known_obstacles.add(planned.destination)
                    else:
                        penalty = 12 if event.reason_code in {
                            "MOVE_CONTESTED",
                            "MOVE_DESTINATION_OCCUPIED",
                            "MOVE_SWAP_BLOCKED",
                        } else 4
                        self.temporary_blocks[planned.destination] = max(
                            self.temporary_blocks.get(planned.destination, 0),
                            turn.tick + penalty,
                        )
            elif event.event_type == "UNIT_MOVE_SUCCEEDED" and actor_key is not None:
                planned = self.planned_moves.pop(actor_key, None)
                if (
                    planned is not None
                    and planned.tick == event.tick
                    and event.position is not None
                    and event.position != planned.destination
                ):
                    self.observations.append(
                        f"manual_override unit={actor_key[:8]} "
                        f"planned={planned.destination} actual={event.position}"
                    )
                    self.decision_totals["manual_override:move"] += 1
                    self.worker_goals.pop(actor_key, None)
            elif event.event_type == "HARVEST_FAILED":
                if event.reason_code in {"RESOURCE_DEPLETED", "NOT_RESOURCE_CELL"}:
                    if event.position is not None:
                        self.resource_last_seen.pop(event.position, None)
                        self.complete_recovery_target(
                            event.position,
                            f"harvest_failed:{event.reason_code}",
                        )
                    if actor_key is not None:
                        self.worker_goals.pop(actor_key, None)
            elif event.event_type == "HARVEST_SUCCEEDED":
                source = (
                    event.harvest_source.value
                    if event.harvest_source is not None
                    else "UNKNOWN"
                )
                amount = event.resource_amount or 0
                self.total_resources_harvested += amount
                self.observations.append(
                    f"harvest_result source={source} amount={amount} at={event.position}"
                )
                self.decision_totals[f"harvest_source:{source}"] += 1
                if event.position is not None and event.harvest_source is HarvestSource.RESOURCE_NODE:
                    self.resource_last_seen.pop(event.position, None)
                    chunk = _chunk_of(event.position)
                    self.chunk_harvests[chunk] += 1
                    self.chunk_anchors[chunk] = event.position
                    self.chunk_next_refill[chunk] = _refill_tick_at_or_after(event.tick)
                if event.position is not None:
                    self.complete_recovery_target(event.position, "harvested")
                if actor_key is not None:
                    self.worker_goals.pop(actor_key, None)
            elif event.event_type == "DEPOSIT_SUCCEEDED" and actor_key is not None:
                self.worker_goals.pop(actor_key, None)
                self.total_resources_deposited += event.resource_amount or 0
            elif event.event_type == "CORE_RESOURCES_CAPTURED":
                self.total_resources_captured += event.resource_amount or 0
            elif (
                event.event_type == "DESTRUCTION_PARTICIPATION"
                and event.reason_code == "CORE"
            ):
                self.enemy_cores_destroyed += 1

        self.known_obstacles.update(turn.obstacle_cells)
        for shot_key, last_tick in tuple(self.shot_miss_ticks.items()):
            if turn.tick - last_tick > RANGER_SHOT_MISS_MEMORY_TICKS:
                self.shot_miss_ticks.pop(shot_key, None)
                self.shot_miss_counts.pop(shot_key, None)
        for axis_key, last_tick in tuple(self.axis_miss_ticks.items()):
            if turn.tick - last_tick > RANGER_SHOT_MISS_MEMORY_TICKS:
                self.axis_miss_ticks.pop(axis_key, None)
                self.axis_miss_counts.pop(axis_key, None)
        visible_enemy_ids = {str(enemy.id) for enemy in turn.visible_enemies}
        if visible_enemy_ids:
            self.last_enemy_visible_tick = turn.tick
        for enemy in turn.visible_enemies:
            # 敌方 Core 坐标不再记入 sightings:先锋猎杀链已废弃,Core 坐标在活跃代码里
            # 零依赖(御驾亲征用 live beacon position、Core 击杀走 enemy_type_snapshot)。
            # 记录只会让陈旧 Core 坐标残留、被未来误用。
            if isinstance(enemy, CoreView):
                continue
            self.enemy_sightings[str(enemy.id)] = EnemySighting(
                position=enemy.position,
                seen_tick=turn.tick,
                is_core=False,
                unit_type=enemy.unit_type.value,
            )
        # 清理 enemy_sightings：(1)非 Core sighting 超过 ASSAULT_SIGHTING_MAX_AGE，
        # (2)当前可见位置但物体不在（确认消失）。Core sighting 不再写入,故无需其分支。
        self.enemy_sightings = {
            object_id: sighting
            for object_id, sighting in self.enemy_sightings.items()
            if (
                not sighting.is_core
                and turn.tick - sighting.seen_tick <= ASSAULT_SIGHTING_MAX_AGE
            )
            and not (
                object_id not in visible_enemy_ids
                and _currently_visible(turn, sighting.position, self.known_obstacles)
            )
        }
        for position in turn.resource_cells:
            self.resource_last_seen[position] = turn.tick

        for position in tuple(self.recovery_targets):
            if (
                position not in turn.resource_cells
                and _currently_visible(turn, position, self.known_obstacles)
            ):
                self.complete_recovery_target(position, "visible_absent")

        visible_absent_resources = {
            position
            for position in self.resource_last_seen
            if position not in turn.resource_cells
            and _currently_visible(turn, position, self.known_obstacles)
        }
        if visible_absent_resources:
            for position in visible_absent_resources:
                self.resource_last_seen.pop(position, None)
            self.worker_goals = {
                unit_id: goal
                for unit_id, goal in self.worker_goals.items()
                if goal.position not in visible_absent_resources
            }
            self.observations.append(
                f"resource_invalidated visible_absent={len(visible_absent_resources)}"
            )
            self.decision_totals["resource:visible_absent"] += len(
                visible_absent_resources
            )

        browser_visible_absent = {
            position
            for position in self.browser_resource_hints
            if position not in turn.resource_cells
            and _currently_visible(turn, position, self.known_obstacles)
        }
        if browser_visible_absent:
            self.browser_resource_hints.difference_update(browser_visible_absent)
            self.worker_goals = {
                unit_id: goal
                for unit_id, goal in self.worker_goals.items()
                if not (
                    goal.kind == "browser_resource_hint"
                    and goal.position in browser_visible_absent
                )
            }
            self.observations.append(
                f"browser_resource_invalidated visible_absent={len(browser_visible_absent)}"
            )
            self.decision_totals["browser_resource:visible_absent"] += len(
                browser_visible_absent
            )

        friendly_positions = {unit.position for unit in turn.units}
        if turn.core is not None:
            friendly_positions.add(turn.core.position)
        self.visited.update(friendly_positions)

        # A friendly object always sees its own cell, so an absent resource there is stale.
        for position in friendly_positions - set(turn.resource_cells):
            self.resource_last_seen.pop(position, None)

        for worker in turn.workers:
            goal = self.worker_goals.get(str(worker.id))
            if goal is None:
                continue
            if worker.position == goal.position:
                if (
                    goal.kind not in {
                        "frontier",
                        "develop_frontier",
                        "resource_sweep",
                        "browser_resource_hint",
                    }
                    and (goal.position not in turn.resource_cells or worker.cargo)
                ):
                    self.worker_goals.pop(str(worker.id), None)
                    if goal.position not in turn.resource_cells:
                        self.resource_last_seen.pop(goal.position, None)

        self.resource_last_seen = {
            position: tick
            for position, tick in self.resource_last_seen.items()
            if turn.tick - tick <= 24
        }
        self.temporary_blocks = {
            position: until
            for position, until in self.temporary_blocks.items()
            if until > turn.tick
        }
        self.planned_moves = {
            unit_id: move
            for unit_id, move in self.planned_moves.items()
            if move.tick >= turn.tick - 1
        }
        if len(self.visited) > 10_000:
            self.visited = Counter(dict(self.visited.most_common(10_000)))
        # 追踪单位位置（用于卡住检测：位置变化时刷新 tick）+ 最近移动方向(惯性)。
        for unit in turn.units:
            uid = str(unit.id)
            previous = self.unit_positions.get(uid)
            self.unit_positions[uid] = unit.position
            if previous != unit.position and previous is not None:
                self.last_position_tick[uid] = turn.tick
                # 推断移动方向(正交一格),用于游侠绕圈惯性防横跳。
                for direction in DIRECTION_ORDER:
                    if _destination(previous, direction) == unit.position:
                        self.unit_headings[uid] = direction
                        break
            recent = self.recent_positions.setdefault(uid, [])
            recent.append(unit.position)
            if len(recent) > STUCK_TICKS:
                del recent[: len(recent) - STUCK_TICKS]
        # 追踪敌人位置（用于预判射击）
        for enemy in turn.visible_enemies:
            eid = str(enemy.id)
            if eid in self.enemy_positions:
                self.enemy_prev[eid] = self.enemy_positions[eid]
            self.enemy_positions[eid] = enemy.position
            # 维护敌方 7 帧轨迹库（仅战斗单位有意义；Core 不入库）。
            if isinstance(enemy, UnitView):
                trail = self.enemy_trails.setdefault(eid, [])
                if not trail or trail[-1] != enemy.position:
                    trail.append(enemy.position)
                if len(trail) > self.ENEMY_TRAIL_WINDOW:
                    del trail[: len(trail) - self.ENEMY_TRAIL_WINDOW]
            if isinstance(enemy, CoreView):
                self.enemy_prev.pop(eid, None)
        for eid in list(self.enemy_positions):
            if eid not in {str(e.id) for e in turn.visible_enemies}:
                self.enemy_positions.pop(eid, None)
                self.enemy_prev.pop(eid, None)
                self.enemy_trails.pop(eid, None)
                # 舞步状态机随敌离视野清掉,防幽灵 phase 锁游侠(配对键含 eid)。
                stale_dance_keys = [
                    key for key in self.vanguard_dance_phase
                    if key.split("|", 1)[1] == eid
                ]
                for key in stale_dance_keys:
                    self.vanguard_dance_phase.pop(key, None)
        # 清理已不存在的单位卡住追踪
        live_ids = {str(u.id) for u in turn.units}
        for uid in list(self.last_position_tick):
            if uid not in live_ids:
                self.last_position_tick.pop(uid, None)
                self.unit_positions.pop(uid, None)
                self.recent_positions.pop(uid, None)
                self.lightning_unit_stuck_counters.pop(uid, None)
                self.lightning_unit_escape_until.pop(uid, None)
        # visited 时间衰减：每 100 tick 对所有 visited 乘以 0.95（指数衰减）
        # 老路径 visited 会缓慢衰减（50 → 1000 tick 后 → 25），相对区分度恢复
        if turn.tick % 100 == 0:
            for pos in list(self.visited.keys()):
                self.visited[pos] = int(self.visited[pos] * 0.95)
                if self.visited[pos] == 0:
                    del self.visited[pos]
        self.last_tick = turn.tick

    def remember_move(self, unit: Unit, destination: Position, tick: int) -> None:
        self.planned_moves[str(unit.id)] = PlannedMove(destination=destination, tick=tick)

    def set_worker_goal(self, worker: Worker, kind: str, position: Position, tick: int) -> None:
        self.worker_goals[str(worker.id)] = WorkerGoal(kind, position, tick)

    def clear_worker_goal(self, worker: Worker) -> None:
        self.worker_goals.pop(str(worker.id), None)

    def clear_comet_state(self) -> None:
        """清除哈雷彗星任务的运行时编队（member/retreating/dispatched_tick）。

        控制参数（comet_active/comet_mode/comet_target/comet_* 数量与阈值）保留，
        由控制文件设定；这里只清掉"本局任务派生出的"瞬时编队状态，避免跨局残留
        锁死单位。comet_target 在 beacon 模式下每 tick 重新算、coordinate 模式下
        由控制文件持有，都不在此清。
        """
        self.comet_member_ids.clear()
        self.comet_retreating_ids.clear()
        self.comet_dispatched_tick = 0
        self.comet_rally_ready_ids.clear()
        self.comet_rally_done = False

    def clear_local_core_sortie(self) -> None:
        self.local_core_sortie_core_id = None
        self.local_core_sortie_position = None
        self.local_core_sortie_started_tick = 0
        self.local_core_sortie_vanguard_ids.clear()
        self.local_core_sortie_ranger_ids.clear()

    def clear_core_shelter_memory(self) -> None:
        self.core_shelter_target = None
        self.core_shelter_entrance = None

    def complete_recovery_target(self, position: Position, reason: str) -> bool:
        if position not in self.recovery_targets:
            return False
        self.recovery_targets = [
            candidate for candidate in self.recovery_targets if candidate != position
        ]
        self.recovery_checked.add(position)
        self.worker_goals = {
            unit_id: goal
            for unit_id, goal in self.worker_goals.items()
            if goal.position != position
        }
        self.observations.append(
            f"resource_recovery_checked target={position} result={reason}"
        )
        self.decision_totals[f"resource_recovery:{reason}"] += 1
        return True

    def load_control(self, path: Path) -> None:
        try:
            if not path.is_file():
                return
            mtime = path.stat().st_mtime_ns
            if mtime == self.control_mtime:
                return
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return
            previous_comet_active = self.comet_active
            self.comet_active = bool(data.get("comet_active", self.comet_active))
            comet_mode = str(data.get("comet_mode", self.comet_mode)) or "beacon"
            self.comet_mode = comet_mode if comet_mode in ("beacon", "coordinate") else "beacon"
            for key in (
                "comet_vanguards",
                "comet_rangers",
                "comet_min_reserve_vanguards",
                "comet_min_reserve_rangers",
            ):
                raw_value = data.get(key)
                if isinstance(raw_value, (int, float)) and not isinstance(
                    raw_value, bool
                ):
                    setattr(self, key, max(0, int(raw_value)))
            threshold = data.get("comet_wounded_threshold")
            if isinstance(threshold, (int, float)) and not isinstance(threshold, bool):
                self.comet_wounded_threshold = max(0.0, min(1.0, float(threshold)))
            rally_enabled_raw = data.get("comet_rally_enabled")
            if isinstance(rally_enabled_raw, bool):
                self.comet_rally_enabled = rally_enabled_raw
            rally_distance_raw = data.get("comet_rally_distance")
            if isinstance(rally_distance_raw, (int, float)) and not isinstance(
                rally_distance_raw, bool
            ):
                self.comet_rally_distance = max(0, int(rally_distance_raw))
            raw_comet_target = data.get("comet_target")
            if (
                isinstance(raw_comet_target, list)
                and len(raw_comet_target) == 2
                and all(
                    isinstance(v, (int, float)) and not isinstance(v, bool)
                    for v in raw_comet_target
                )
            ):
                self.comet_target = (
                    int(raw_comet_target[0]),
                    int(raw_comet_target[1]),
                )
            elif self.comet_mode == "coordinate":
                self.comet_target = None
            # 任务从开启→关闭（或反向）时，清掉上一局的编队，防止残留锁死。
            if previous_comet_active != self.comet_active:
                self.clear_comet_state()
            for key in ("aggress_vanguards", "aggress_rangers"):
                raw_value = data.get(key)
                if isinstance(raw_value, (int, float)) and not isinstance(
                    raw_value, bool
                ):
                    setattr(self, key, max(0, int(raw_value)))
            # === 网页控制台新增控制字段（旧代理忽略未知 key，向后兼容）===
            raw_orbit = data.get("core_orbit_radius", 0)
            if (
                isinstance(raw_orbit, (int, float))
                and not isinstance(raw_orbit, bool)
                and raw_orbit >= 0
            ):
                new_orbit = int(raw_orbit)
                # r 变更检测：仅当 r 从旧值变到不同的新值(且新值>0)时,标记待迁移。
                # 实际写入 core_target 在 choose_actions 里做(那里有 turn.core.position)。
                # r=0 视为"未设置/关闭巡逻"，不触发迁移。
                if new_orbit != self.core_orbit_radius and new_orbit > 0:
                    self.core_orbit_migration_pending = True
                self.core_orbit_radius = new_orbit
            self.core_hold = bool(data.get("core_hold", self.core_hold))
            raw_target = data.get("core_target")
            if raw_target is None:
                self.core_target = None
            elif (
                isinstance(raw_target, list)
                and len(raw_target) == 2
                and all(
                    isinstance(v, (int, float)) and not isinstance(v, bool)
                    for v in raw_target
                )
            ):
                self.core_target = (int(raw_target[0]), int(raw_target[1]))
                # 用户从控制文件设的目标 → 到达后应 auto 驻扎。
                self.core_target_kind = "user"
            raw_mode = data.get("core_transfer_mode", "star")
            if raw_mode in TRANSFER_MODES:
                self.core_transfer_mode = raw_mode
            self.core_evade_enemies = bool(data.get("core_evade_enemies", self.core_evade_enemies))
            self.core_chase_enemies = bool(data.get("core_chase_enemies", self.core_chase_enemies))
            self.core_pursue_beacon = bool(data.get("core_pursue_beacon", self.core_pursue_beacon))
            # 关闭御驾亲征时清掉 beacon 目标，回落恒星巡逻/用户手设目标。
            # （core_target_kind=="beacon" 只由御驾亲征在 choose_actions 里写入。）
            if not self.core_pursue_beacon and self.core_target_kind == "beacon":
                self.core_target = None
                self.core_target_kind = "user"
            raw_queue = data.get("build_queue")
            if isinstance(raw_queue, list):
                queue: list[str] = []
                for item in raw_queue[:MAX_BUILD_QUEUE_LENGTH]:
                    if isinstance(item, str) and item in CONTROL_UNIT_TYPES:
                        queue.append(item)
                self.build_queue = queue
            # 开局引导计数：重启后保留进度，避免重新固定造 3 工人。
            raw_bootstrap = data.get("bootstrap_workers_built")
            if isinstance(raw_bootstrap, (int, float)) and not isinstance(raw_bootstrap, bool):
                self.bootstrap_workers_built = max(0, min(3, int(raw_bootstrap)))
            raw_ratio = data.get("spawn_ratio")
            if isinstance(raw_ratio, dict):
                # 比例允许全 0（停止造兵囤资源）；某兵种=0 停造该兵种。
                # 各 key 缺省即用默认比例 1:1:3（游侠:先锋:工人）。
                new_ratio: dict[str, int] = {}
                for key, default in (
                    ("ranger", 1),
                    ("vanguard", 1),
                    ("worker", 3),
                ):
                    raw_value = raw_ratio.get(key, default)
                    if (
                        isinstance(raw_value, int)
                        and not isinstance(raw_value, bool)
                        and raw_value >= 0
                    ):
                        new_ratio[key] = raw_value
                    else:
                        new_ratio[key] = default
                self.spawn_ratio = {
                    "ranger": new_ratio["ranger"],
                    "vanguard": new_ratio["vanguard"],
                    "worker": new_ratio["worker"],
                }
            raw_caps = data.get("unit_caps")
            if isinstance(raw_caps, dict):
                caps: dict[str, int] = {}
                for key in ("worker", "vanguard", "ranger"):
                    raw_value = raw_caps.get(key, 0)
                    if (
                        isinstance(raw_value, int)
                        and not isinstance(raw_value, bool)
                        and raw_value >= 0
                    ):
                        caps[key] = raw_value
                if caps:
                    self.unit_caps = {
                        key: caps.get(key, 0)
                        for key in ("worker", "vanguard", "ranger")
                    }
            # 补兵阈值：各兵种 < 阈值时优先补。0 = 不主动补该兵种。
            raw_threshold = data.get("replenish_threshold")
            if isinstance(raw_threshold, dict):
                self.replenish_threshold = {
                    key: max(0, int(raw_threshold.get(key, 0)))
                    for key in ("ranger", "vanguard", "worker")
                }
            # 补兵优先级：多兵种同时低于阈值时按此顺序补。
            raw_priority = data.get("replenish_priority")
            if isinstance(raw_priority, list):
                priority = [
                    str(item).lower()
                    for item in raw_priority
                    if isinstance(item, str)
                    and item.lower() in ("ranger", "vanguard", "worker")
                ]
                for key in ("ranger", "worker", "vanguard"):
                    if key not in priority:
                        priority.append(key)
                self.replenish_priority = priority
            raw_reserve = data.get("wartime_reserve")
            if (
                isinstance(raw_reserve, (int, float))
                and not isinstance(raw_reserve, bool)
                and raw_reserve >= 0
            ):
                self.wartime_reserve = int(raw_reserve)
            self.control_mtime = mtime
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            pass

    def refresh_recovery_target_hints(self, path: Path | None = None) -> None:
        target_path = path or Path(
            os.environ.get(
                "ARENA_HERO_RECOVERY_TARGETS_FILE",
                RECOVERY_TARGETS_FILENAME,
            )
        )
        configured = _load_recovery_target_hints(target_path)
        if configured is None:
            return
        configured_set = set(configured)
        self.recovery_targets = [
            position
            for position in self.recovery_targets
            if position in configured_set
        ]
        active_targets = set(self.recovery_targets)
        self.worker_goals = {
            unit_id: goal
            for unit_id, goal in self.worker_goals.items()
            if not (
                goal.kind == "resource_recovery"
                and goal.position not in configured_set
            )
        }
        ordered_targets = list(configured)
        for position in ordered_targets:
            if (
                position not in self.recovery_checked
                and position not in active_targets
            ):
                self.recovery_targets.append(position)
                active_targets.add(position)

    def refresh_browser_intel(self, path: Path | None = None) -> None:
        """Load expiring browser-map coordinates as low-confidence hints."""
        self.browser_resource_hints.clear()
        self.browser_intel_captured_at = None
        self.browser_intel_age_seconds = 0
        self.browser_intel_online = False
        intel_path = path or Path(
            os.environ.get("ARENA_HERO_BROWSER_INTEL_FILE", BROWSER_INTEL_FILENAME)
        )
        try:
            data = json.loads(intel_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("version") != 1:
                return
            captured_at = data.get("captured_at")
            if not isinstance(captured_at, str):
                return
            parsed = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            age = max(0, int(time.time() - parsed.timestamp()))
            self.browser_intel_captured_at = captured_at[:64]
            self.browser_intel_age_seconds = age
            if age > BROWSER_INTEL_MAX_AGE_SECONDS:
                return
            self.browser_intel_online = True
            raw_resources = data.get("resources", [])
            if not isinstance(raw_resources, list):
                return
            candidate_hints: set[Position] = set()
            for value in raw_resources[:4096]:
                if (
                    isinstance(value, list)
                    and len(value) == 2
                    and all(
                        isinstance(item, int) and not isinstance(item, bool)
                        for item in value
                    )
                ):
                    candidate_hints.add((int(value[0]), int(value[1])))
            if BROWSER_RESOURCE_REQUIRE_QUOTA_PLAUSIBILITY:
                per_chunk = Counter(_chunk_of(position) for position in candidate_hints)
                if any(
                    count > _chunk_quota(chunk)
                    for chunk, count in per_chunk.items()
                ):
                    return
            self.browser_resource_hints.update(candidate_hints)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return

    def write_stats(self, path: Path, turn: Turn) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            core_position = list(turn.core.position) if turn.core is not None else None
            core_state = (
                turn.core.view.state.value if turn.core is not None else "RESPAWNING"
            )
            elapsed_ticks = (
                max(0, turn.tick - self.first_observed_tick + 1)
                if self.first_observed_tick > 0
                else 0
            )
            payload = {
                # Lightning is force-dispatched; report that truth rather than
                # exposing retired control-file modes in operational telemetry.
                "mode": MODE_LIGHTNING,
                "tick": turn.tick,
                "comet_active": self.comet_active,
                "comet_mode": self.comet_mode,
                "comet_target": (
                    list(self.comet_target)
                    if self.comet_target is not None
                    else None
                ),
                "comet_vanguards": self.comet_vanguards,
                "comet_rangers": self.comet_rangers,
                "comet_min_reserve_vanguards": self.comet_min_reserve_vanguards,
                "comet_min_reserve_rangers": self.comet_min_reserve_rangers,
                "comet_wounded_threshold": self.comet_wounded_threshold,
                "comet_selected_vanguards": sum(
                    1
                    for uid in self.comet_member_ids
                    if uid in self.unit_labels
                    and self.unit_labels[uid].object_type == "VANGUARD"
                ),
                "comet_selected_rangers": sum(
                    1
                    for uid in self.comet_member_ids
                    if uid in self.unit_labels
                    and self.unit_labels[uid].object_type == "RANGER"
                ),
                "comet_retreating": len(self.comet_retreating_ids),
                "comet_dispatched_tick": self.comet_dispatched_tick,
                "comet_rally_enabled": self.comet_rally_enabled,
                "comet_rally_distance": self.comet_rally_distance,
                "comet_rally_ready": len(self.comet_rally_ready_ids),
                "resources": turn.resources,
                "capacity": turn.resource_capacity,
                "population": len(turn.units),
                "workers": len(turn.workers),
                "vanguards": len(turn.vanguards),
                "rangers": len(turn.rangers),
                "core_hp": turn.core.hp if turn.core else 0,
                "core_shield": turn.core.shield if turn.core else 0,
                "core_state": core_state,
                "core_position": core_position,
                "beacon_position": list(turn.beacon.position),
                "beacon_status": (
                    turn.beacon.status.value
                    if turn.beacon.status is not None
                    else "UNCLAIMED"
                ),
                "visible_enemies": len(turn.visible_enemies),
                "core_threat_count": sum(
                    1
                    for enemy in turn.visible_enemies
                    if isinstance(enemy, UnitView)
                    and enemy.unit_type in {UnitType.VANGUARD, UnitType.RANGER}
                    and turn.core is not None
                    and _distance(enemy.position, turn.core.position)
                    <= AGGRESS_CORE_ALERT_RADIUS
                ),
                "core_reinforcement_active": False,
                "core_recovery_active": (
                    turn.core is not None
                    and (
                        (
                            self.last_core_damaged_tick > 0
                            and turn.tick - self.last_core_damaged_tick
                            <= CORE_DAMAGE_EMERGENCY_TICKS
                        )
                        or (
                            max(
                                self.last_core_destroyed_tick,
                                self.last_core_respawn_tick,
                            )
                            > 0
                            and turn.tick
                            - max(
                                self.last_core_destroyed_tick,
                                self.last_core_respawn_tick,
                            )
                            <= CORE_RECOVERY_REBUILD_TICKS
                        )
                        or len(turn.vanguards) + len(turn.rangers)
                        < HOME_RESERVE_COMBAT
                    )
                ),
                "last_core_damaged_tick": self.last_core_damaged_tick,
                "last_core_destroyed_tick": self.last_core_destroyed_tick,
                "last_core_respawn_tick": self.last_core_respawn_tick,
                "owns_beacon": _owns_beacon(turn),
                "visible_resource_cells": len(turn.resource_cells),
                "known_resource_cells": len(self.resource_last_seen),
                "browser_resource_hints": len(self.browser_resource_hints),
                "browser_intel_age_seconds": self.browser_intel_age_seconds,
                "browser_intel_online": self.browser_intel_online,
                "known_obstacle_cells": len(self.known_obstacles),
                "visited_cells": len(self.visited),
                "worker_cargo": sum(worker.cargo for worker in turn.workers),
                "active_routes": len(self.current_routes),
                "complete_routes": sum(
                    1 for route in self.current_routes.values() if route.complete
                ),
                "remembered_enemies": len(self.enemy_sightings),
                "exploring_workers": sum(
                    1
                    for goal in self.worker_goals.values()
                    if goal.kind in {"develop_frontier", "resource_sweep"}
                ),
                "max_worker_search_radius": max(
                    self.worker_search_radius.values(),
                    default=0,
                ),
                "tick_interval": self.current_tick_interval,
                "observed_turns": self.observed_turns,
                "elapsed_ticks": elapsed_ticks,
                "total_resources_harvested": self.total_resources_harvested,
                "total_resources_deposited": self.total_resources_deposited,
                "total_resources_captured": self.total_resources_captured,
                "enemy_cores_destroyed": self.enemy_cores_destroyed,
                "up_time": elapsed_ticks,
                "units_lost": self.units_lost,
                "replacement_queue": dict(sorted(self.replacement_queue.items())),
                "units_built": self.event_totals.get("CORE_SPAWN_SUCCEEDED", 0),
                "core_events": int(
                    self.event_totals.get("CORE_RESOURCES_CAPTURED", 0)
                    + self.event_totals.get("CORE_RESOURCE_OVERFLOW_DESTROYED", 0)
                    + self.event_totals.get("CORE_MOVE_STARTED", 0)
                    + self.event_totals.get("CORE_MOVE_CANCELLED", 0)
                ),
                "harvest_count": self.decision_totals.get("worker:harvest", 0),
                "deposit_count": self.decision_totals.get("worker:deposit", 0),
                "shoot_count": self.decision_totals.get("ranger:shot", 0),
                "shots_fired": self.decision_totals.get("ranger:shot", 0),
                "shots_hit": self.event_totals.get("SHOT_HIT", 0),
                "standoff_engagements": self.decision_totals.get("ranger:standoff_engaged", 0),
                "blind_fires": self.decision_totals.get("ranger:blind_fire", 0),
                "diagonal_supports": self.decision_totals.get("ranger:diagonal_support", 0),
                "vanguard_dance_steps": self.decision_totals.get("ranger:vanguard_dance", 0),
                "ambush_trades": self.decision_totals.get("ranger:ambush_trade", 0),
                "move_failures": self.event_totals.get("UNIT_MOVE_FAILED", 0),
                "manual_overrides": self.decision_totals.get(
                    "manual_override:move", 0
                ),
                "event_totals": dict(sorted(self.event_totals.items())),
                "decision_totals": dict(sorted(self.decision_totals.items())),
                "recent_events": self.last_events[-15:],
                "units": [
                    {
                        "id": str(unit.id)[:8],
                        "type": unit.unit_type.value,
                        "number": self.unit_labels.get(
                            str(unit.id), UnitLabel(unit.unit_type.value, 0)
                        ).number,
                        "position": [unit.position[0], unit.position[1]],
                        "hp": unit.hp,
                    }
                    for unit in sorted(
                        turn.units,
                        key=lambda candidate: (
                            candidate.unit_type.value,
                            self.unit_labels.get(
                                str(candidate.id), UnitLabel(candidate.unit_type.value, 0)
                            ).number,
                            candidate.id.bytes,
                        ),
                    )
                ],
                # 可见敌方单位（地图红点），上限 64。
                "enemy_units": [
                    {
                        "id": str(enemy.id)[:8],
                        "type": (
                            "CORE"
                            if isinstance(enemy, CoreView)
                            else enemy.unit_type.value
                        ),
                        "position": [enemy.position[0], enemy.position[1]],
                        "hp": enemy.hp,
                    }
                    for enemy in turn.visible_enemies[:64]
                ],
                # 生效配置回显（前端显示"已生效值"）。
                "effective_control": {
                    "core_orbit_radius": self.core_orbit_radius,
                    "core_hold": self.core_hold,
                    "core_target": (
                        list(self.core_target)
                        if self.core_target is not None
                        else None
                    ),
                    "core_target_kind": self.core_target_kind,
                    "core_transfer_mode": self.core_transfer_mode,
                    "core_evade_enemies": self.core_evade_enemies,
                    "core_chase_enemies": self.core_chase_enemies,
                    "core_pursue_beacon": self.core_pursue_beacon,
                    "build_queue": list(self.build_queue),
                    "bootstrap_workers_built": self.bootstrap_workers_built,
                    "spawn_ratio": dict(self.spawn_ratio),
                    "unit_caps": dict(self.unit_caps),
                    "replenish_threshold": dict(self.replenish_threshold),
                    "replenish_priority": list(self.replenish_priority),
                    "wartime_reserve": self.wartime_reserve,
                },
            }
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            temporary.replace(path)
        except OSError:
            pass


def _distance(left: Position, right: Position) -> int:
    return abs(left[0] - right[0]) + abs(left[1] - right[1])


def _unit_type_from_name(name: str) -> UnitType | None:
    """控制文件里兵种字符串("WORKER"/"VANGUARD"/"RANGER") → UnitType，非法返回 None。"""
    try:
        return UnitType(name)
    except (ValueError, TypeError):
        return None


def _shot_cell_key(target_id: UUID, cell: Position) -> str:
    return f"{target_id}|{cell[0]}|{cell[1]}"


def _shot_axis_key(target_id: UUID | str, target_pos: Position, expected_cell: Position) -> str | None:
    """射击格相对敌人当前格的主轴：dx 主导记 "x"，dy 主导记 "y"。

    敌人若卡格未动（dx=dy=0）返回 None——此时无法用轴区分，跳过聚合。
    """
    dx = expected_cell[0] - target_pos[0]
    dy = expected_cell[1] - target_pos[1]
    if abs(dx) >= abs(dy) and dx != 0:
        return f"{target_id}|x"
    if abs(dy) > abs(dx) and dy != 0:
        return f"{target_id}|y"
    return None


def _cell_sort_key(cell: Position) -> tuple[int, int]:
    """格的确定性排序键(供打分平手决胜用)。"""
    return (cell[0], cell[1])


def _core_attack_surface_profile(
    anchor: Position,
    obstacles: set[Position],
) -> tuple[int, Position | None, int, int]:
    """Trace the Ranger's eight rays; rocks block every farther cell on a ray."""
    open_ranged_offsets: list[Position] = []
    melee_open = 0
    for dx, dy in RANGER_LINE_DELTAS:
        for distance in range(1, MIGRATION_SITE_RADIUS + 1):
            position = (
                anchor[0] + dx * distance,
                anchor[1] + dy * distance,
            )
            if position in obstacles:
                break
            if distance == 1:
                melee_open += 1
            else:
                open_ranged_offsets.append((dx * distance, dy * distance))
    best_axis: Position | None = None
    best_count = -1
    for axis_x, axis_y in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        count = sum(
            offset_x * axis_x + offset_y * axis_y >= 0
            for offset_x, offset_y in open_ranged_offsets
        )
        if count > best_count:
            best_axis = (axis_x, axis_y)
            best_count = count
    return (
        len(open_ranged_offsets),
        best_axis,
        max(0, best_count),
        melee_open,
    )


def _terrain_guard_offsets(
    anchor: Position,
    obstacles: set[Position],
    offsets: tuple[Position, ...],
) -> tuple[Position, ...]:
    """Prefer Core guard slots on the open half of a rock-backed position."""
    open_count, open_axis, concentrated_count, _ = _core_attack_surface_profile(
        anchor,
        obstacles,
    )
    if (
        open_axis is None
        or open_count > MIGRATION_SITE_MAX_OPEN_RANGED_CELLS
        or concentrated_count * MIGRATION_SITE_MIN_OPEN_HALF_RATIO_DENOMINATOR
        < open_count * MIGRATION_SITE_MIN_OPEN_HALF_RATIO_NUMERATOR
    ):
        return offsets
    axis_x, axis_y = open_axis
    open_half = [
        offset
        for offset in offsets
        if offset[0] * axis_x + offset[1] * axis_y >= 0
    ]
    blocked_half = [offset for offset in offsets if offset not in open_half]
    return tuple(open_half + blocked_half)


def _sign(value: int) -> int:
    """返回 -1 / 0 / +1（用于编队方向偏移）"""
    return (value > 0) - (value < 0)


def _load_recovery_target_hints(path: Path) -> tuple[Position, ...] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") != 1:
            return None
        targets: list[Position] = []
        for value in data.get("targets", ()):
            if not isinstance(value, list) or len(value) != 2:
                continue
            position = int(value[0]), int(value[1])
            if position not in targets:
                targets.append(position)
        return tuple(targets)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _chunk_of(position: Position) -> Chunk:
    return position[0] // CHUNK_SIZE, position[1] // CHUNK_SIZE


def _chunk_quota(chunk: Chunk) -> int:
    def axis(value: int) -> int:
        return value if value >= 0 else -value - 1

    ring = axis(chunk[0]) + axis(chunk[1])
    return max(2, (16 * 8) // (8 + ring))


def _refill_tick_at_or_after(tick: int) -> int:
    return tick + ((4 - tick % 4) % 4)


def _destination(position: Position, direction: Direction) -> Position:
    dx, dy = direction.delta
    return position[0] + dx, position[1] + dy


def _route_positions(
    start: Position,
    directions: Iterable[Direction],
) -> tuple[Position, ...]:
    positions = [start]
    current = start
    for direction in directions:
        current = _destination(current, direction)
        positions.append(current)
    return tuple(positions)


def _short_id(value: UUID) -> str:
    return str(value)[:8]


def _uuid_key(obj: Unit | UnitView | CoreView) -> bytes:
    return obj.id.bytes


def _owns_beacon(turn: Turn) -> bool:
    if turn.beacon.status is not BeaconStatus.CARRIED:
        return False
    owned_ids = {unit.id for unit in turn.units}
    if turn.core is not None:
        owned_ids.add(turn.core.id)
    return turn.beacon.carrier_id in owned_ids


def _refill_probe_allowed(
    origin: Position,
    target: Position,
    beacon: Position | None,
) -> bool:
    travel_distance = _distance(origin, target)
    if travel_distance > REFILL_PROBE_MAX_DISTANCE:
        return False
    if beacon is None or _distance(target, beacon) <= _distance(origin, beacon):
        return True
    return travel_distance <= REFILL_PROBE_BACKTRACK_DISTANCE


def _last_seen_resource_allowed(
    origin: Position,
    target: Position,
    beacon: Position,
) -> bool:
    travel_distance = _distance(origin, target)
    if travel_distance > LAST_SEEN_RESOURCE_MAX_DISTANCE:
        return False
    if _distance(target, beacon) <= _distance(origin, beacon):
        return True
    return travel_distance <= LAST_SEEN_RESOURCE_BACKTRACK_DISTANCE


def _line_clear(origin: Position, target: Position, obstacles: set[Position]) -> bool:
    delta_x = target[0] - origin[0]
    delta_y = target[1] - origin[1]
    if delta_x != 0 and delta_y != 0 and abs(delta_x) != abs(delta_y):
        return False
    dx = 0 if delta_x == 0 else (1 if delta_x > 0 else -1)
    dy = 0 if delta_y == 0 else (1 if delta_y > 0 else -1)
    cell = (origin[0] + dx, origin[1] + dy)
    while cell != target:
        if cell in obstacles:
            return False
        cell = (cell[0] + dx, cell[1] + dy)
    return True


def _vision_line_clear(
    origin: Position,
    target: Position,
    obstacles: set[Position],
) -> bool:
    """Match obstacle blocking for the server's integer supercover vision line."""

    if origin == target:
        return True

    delta_x = target[0] - origin[0]
    delta_y = target[1] - origin[1]
    step_x = 0 if delta_x == 0 else (1 if delta_x > 0 else -1)
    step_y = 0 if delta_y == 0 else (1 if delta_y > 0 else -1)
    width = abs(delta_x)
    height = abs(delta_y)
    crossed_x = 0
    crossed_y = 0
    x, y = origin

    while crossed_x < width or crossed_y < height:
        x_boundary = (1 + 2 * crossed_x) * height
        y_boundary = (1 + 2 * crossed_y) * width

        if x_boundary == y_boundary:
            side_x = (x + step_x, y)
            side_y = (x, y + step_y)
            if side_x in obstacles or side_y in obstacles:
                return False
            x += step_x
            y += step_y
            crossed_x += 1
            crossed_y += 1
        elif x_boundary < y_boundary:
            x += step_x
            crossed_x += 1
        else:
            y += step_y
            crossed_y += 1

        position = (x, y)
        if position == target:
            return True
        if position in obstacles:
            return False

    return True


def _currently_visible(turn: Turn, position: Position, obstacles: set[Position]) -> bool:
    observers: list[tuple[Position, int]] = []
    if turn.core is not None:
        observers.append((turn.core.position, CORE_VISION_RADIUS))
    observers.extend(
        (unit.position, UNIT_VISION_RADIUS[unit.unit_type])
        for unit in turn.units
    )
    return any(
        _distance(origin, position) <= radius
        and _vision_line_clear(origin, position, obstacles)
        for origin, radius in observers
    )


def _enemy_watchers(turn: Turn) -> tuple[tuple[Position, int], ...]:
    """敌方视角观察者：仅游侠(R5)与先锋(R4)。敌方工人与敌方 Core 不参与。

    我方是全图视野，敌方视野有限；站在这些观察者看不见的格子里开火，
    敌方不会预瞄我方——可能无伤命中（用户拍板：只算敌方游侠+先锋）。
    """
    return tuple(
        (enemy.position, UNIT_VISION_RADIUS[enemy.unit_type])
        for enemy in turn.visible_enemies
        if isinstance(enemy, UnitView)
        and enemy.unit_type in {UnitType.RANGER, UnitType.VANGUARD}
    )


def _enemy_can_see_cell(
    watchers: tuple[tuple[Position, int], ...],
    cell: Position,
    obstacles: set[Position],
) -> bool:
    """该格是否在敌方任一游侠/先锋视野内（半径+障碍视线遮挡）。"""
    return any(
        _distance(origin, cell) <= radius
        and _vision_line_clear(origin, cell, obstacles)
        for origin, radius in watchers
    )


def _unit_can_see_position(
    unit: Unit,
    position: Position,
    obstacles: set[Position],
) -> bool:
    return (
        _distance(unit.position, position) <= UNIT_VISION_RADIUS[unit.unit_type]
        and _vision_line_clear(unit.position, position, obstacles)
    )


def _shelter_entrance(
    position: Position,
    obstacles: set[Position],
) -> Position | None:
    """Return the sole cardinal entrance of a three-sided obstacle pocket."""
    open_neighbors = [
        _destination(position, direction)
        for direction in DIRECTION_ORDER
        if _destination(position, direction) not in obstacles
    ]
    return open_neighbors[0] if len(open_neighbors) == 1 else None


def _core_logistics_corridor(
    position: Position,
    obstacles: set[Position],
    *,
    length: int = CORE_LOGISTICS_CORRIDOR_LENGTH,
) -> frozenset[Position]:
    """Return the outward cells that must stay open for a one-door Core."""
    entrance = _shelter_entrance(position, obstacles)
    if entrance is None:
        return frozenset()
    step_x = entrance[0] - position[0]
    step_y = entrance[1] - position[1]
    return frozenset(
        (
            position[0] + step_x * distance,
            position[1] + step_y * distance,
        )
        for distance in range(1, max(1, length) + 1)
    )


def _is_legal_ranger_shot(
    origin: Position,
    target: Position,
    obstacles: set[Position],
) -> bool:
    delta_x = abs(target[0] - origin[0])
    delta_y = abs(target[1] - origin[1])
    if delta_x != 0 and delta_y != 0 and delta_x != delta_y:
        return False
    line_distance = max(delta_x, delta_y)
    return 1 <= line_distance <= 3 and _line_clear(origin, target, obstacles)


def _effective_hp(enemy: UnitView | CoreView) -> int:
    return enemy.hp + (enemy.shield if isinstance(enemy, CoreView) else 0)


def _enemy_role_priority(enemy: UnitView | CoreView) -> int:
    if isinstance(enemy, UnitView) and enemy.unit_type is UnitType.RANGER:
        return 0
    if isinstance(enemy, UnitView) and enemy.unit_type is UnitType.VANGUARD:
        return 1
    if isinstance(enemy, CoreView):
        return 2
    return 3


def _build_threat_map(turn: Turn, obstacles: set[Position]) -> Counter[Position]:
    threat: Counter[Position] = Counter()
    for enemy in turn.visible_enemies:
        if not isinstance(enemy, UnitView):
            continue
        if enemy.unit_type is UnitType.VANGUARD:
            for direction in DIRECTION_ORDER:
                threat[_destination(enemy.position, direction)] += 3
        elif enemy.unit_type is UnitType.RANGER:
            for dx, dy in RANGER_LINE_DELTAS:
                cell = enemy.position
                for _ in range(3):
                    cell = (cell[0] + dx, cell[1] + dy)
                    if cell in obstacles:
                        break
                    threat[cell] += 2
    return threat


def _find_path(
    start: Position,
    goal: Position,
    *,
    blocked: set[Position],
    threat: Counter[Position],
    visited: Counter[Position],
    max_expansions: int = 30000,
    ignore_occupancy_goals: bool = True,
) -> tuple[Direction, ...]:
    if start == goal:
        return ()

    search_radius = max(32, min(400, _distance(start, goal) + 60))
    frontier: list[tuple[float, float, int, Position]] = []
    sequence = 0
    heapq.heappush(frontier, (float(_distance(start, goal)), 0.0, sequence, start))
    costs: dict[Position, float] = {start: 0.0}
    came_from: dict[Position, tuple[Position, Direction]] = {}
    expansions = 0

    while frontier and expansions < max_expansions:
        _, current_cost, _, current = heapq.heappop(frontier)
        if current == goal:
            directions: list[Direction] = []
            while current != start:
                previous, direction = came_from[current]
                directions.append(direction)
                current = previous
            directions.reverse()
            return tuple(directions)
        if current_cost > costs.get(current, float("inf")):
            continue

        expansions += 1
        for direction in DIRECTION_ORDER:
            nxt = _destination(current, direction)
            if nxt != goal and nxt in blocked:
                continue
            if _distance(start, nxt) > search_radius:
                continue
            step_cost = 1.0 + threat.get(nxt, 0) * 4.0 + min(3.0, visited.get(nxt, 0) * 0.08)
            new_cost = current_cost + step_cost
            if new_cost >= costs.get(nxt, float("inf")):
                continue
            costs[nxt] = new_cost
            came_from[nxt] = (current, direction)
            sequence += 1
            priority = new_cost + _distance(nxt, goal)
            heapq.heappush(frontier, (priority, new_cost, sequence, nxt))
    return ()


class MovementPlanner:
    def __init__(self, turn: Turn, memory: TacticMemory, decisions: list[str]) -> None:
        self.turn = turn
        self.memory = memory
        self.decisions = decisions
        self.obstacles = set(memory.known_obstacles) | set(turn.obstacle_cells)
        self.enemy_cells = {enemy.position for enemy in turn.visible_enemies}
        self.threat = _build_threat_map(turn, self.obstacles)
        self.occupancy: Counter[Position] = Counter(unit.position for unit in turn.units)
        if turn.core is not None:
            self.occupancy[turn.core.position] += 1
        self.departures: Counter[Position] = Counter()
        self.arrivals: Counter[Position] = Counter()

    def final_occupancy(self, position: Position) -> int:
        return self.occupancy[position] - self.departures[position] + self.arrivals[position]

    def _can_enter(self, position: Position) -> bool:
        return (
            position not in self.obstacles
            and position not in self.enemy_cells
            and self.memory.temporary_blocks.get(position, 0) <= self.turn.tick
            # 规则：每格最多容纳两个实体（Core/Unit 各占一个名额）。
            # 之前使用 <3 会把第三个单位也排进计划，服务器随后以
            # CELL_UNIT_LIMIT 拒绝，形成假性“卡死”。
            and self.final_occupancy(position) < 2
        )

    def _blocked(
        self,
        unit: Unit,
        goal: Position,
        avoid: frozenset[Position],
    ) -> set[Position]:
        blocked = set(self.obstacles) | set(self.enemy_cells)
        blocked.update(avoid)
        blocked.update(
            position
            for position, until in self.memory.temporary_blocks.items()
            if until > self.turn.tick
        )
        blocked.update(
            position
            for position in self.occupancy
            if position != unit.position and position != goal and self.final_occupancy(position) >= 2
        )
        return blocked

    def _queue(
        self,
        unit: Unit,
        direction: Direction,
        reason: str,
        avoid: frozenset[Position] = frozenset(),
        goal: Position | None = None,
        route: tuple[Position, ...] | None = None,
        route_complete: bool = False,
    ) -> bool:
        origin = unit.position
        destination = _destination(origin, direction)
        if destination in avoid or not self._can_enter(destination):
            return False
        unit.move(direction)
        self.departures[origin] += 1
        self.arrivals[destination] += 1
        self.memory.remember_move(unit, destination, self.turn.tick)
        route_path = route or (origin, destination)
        if (
            len(route_path) < 2
            or route_path[0] != origin
            or route_path[1] != destination
        ):
            route_path = (origin, destination)
            route_complete = False
        self.memory.current_routes[str(unit.id)] = PlannedRoute(
            object_id=str(unit.id),
            object_type=unit.view.unit_type.value,
            start=origin,
            goal=goal,
            path=route_path,
            reason=reason,
            complete=route_complete and goal is not None and route_path[-1] == goal,
        )
        goal_text = f" goal={goal}" if goal is not None else ""
        self.decisions.append(
            f"{unit.view.unit_type.value.lower()}:{_short_id(unit.id)} move {direction.value} "
            f"to={destination}{goal_text} reason={reason}"
        )
        self.memory.decision_totals[f"move:{reason}"] += 1
        return True

    def toward(
        self,
        unit: Unit,
        goal: Position,
        reason: str,
        *,
        avoid: Iterable[Position] = (),
    ) -> bool:
        if unit.position == goal:
            return False
        avoid_cells = frozenset(avoid)
        path = _find_path(
            unit.position,
            goal,
            blocked=self._blocked(unit, goal, avoid_cells),
            threat=self.threat,
            visited=self.memory.visited,
        )
        route = _route_positions(unit.position, path)
        if path and self._queue(
            unit,
            path[0],
            reason,
            avoid_cells,
            goal,
            route,
            route[-1] == goal,
        ):
            return True

        candidates = sorted(
            DIRECTION_ORDER,
            key=lambda direction: (
                self.threat.get(_destination(unit.position, direction), 0),
                _distance(_destination(unit.position, direction), goal),
                self.memory.visited.get(_destination(unit.position, direction), 0),
                self.memory.temporary_blocks.get(
                    _destination(unit.position, direction), 0
                ) > self.turn.tick,
                DIRECTION_RANK[direction],
            ),
        )
        return any(
            self._queue(unit, direction, reason + ":fallback", avoid_cells, goal)
            for direction in candidates
        )

    def eta(self, unit: Unit, goal: Position, *, avoid: Iterable[Position] = ()) -> int:
        """Return an occupancy/obstacle-aware movement ETA without reserving a move.

        Dynamic orbital boundaries tell us *which* ring matters; this estimate
        tells relief and funnel planners whether a unit can physically reach it.
        """
        if unit.position == goal:
            return 0
        path = _find_path(
            unit.position,
            goal,
            blocked=self._blocked(unit, goal, frozenset(avoid)),
            threat=self.threat,
            visited=self.memory.visited,
        )
        return len(path) if path else _distance(unit.position, goal)

    def flee_from_combat(
        self,
        unit: Unit,
        enemies: Iterable[UnitView],
        core_position: Position | None,
        reason: str,
    ) -> bool:
        """撤离真正的战斗接触，而不是只看当前格是否已被覆盖。

        旧逻辑只在 ``planner.threat[unit.position]`` 非零时调用 flee，
        且货物工人在这段判断之前就进入回城路线。因此敌方游侠/先锋
        刚进入视野但尚未覆盖当前格时，工人仍会继续向资源或 Core 走，
        下一步才一头扎进火力线。这里把敌方战斗单位本身作为撤退依据，
        同时考虑火力威胁、离敌距离、出口数量和回 Core 方向。
        """
        combat_enemies = tuple(enemies)
        threat_cells = tuple(enemy.position for enemy in combat_enemies)
        if not threat_cells:
            return False

        def score(direction: Direction) -> tuple[int, int, int, int, int, int, int]:
            destination = _destination(unit.position, direction)
            exits = sum(
                1
                for candidate_direction in DIRECTION_ORDER
                if self._can_enter(_destination(destination, candidate_direction))
            )
            enemy_distance = min(
                _distance(destination, enemy_position)
                for enemy_position in threat_cells
            )
            core_distance = (
                _distance(destination, core_position)
                if core_position is not None
                else 0
            )
            return (
                self.threat.get(destination, 0),
                -enemy_distance,
                -exits,
                core_distance,
                self.memory.visited.get(destination, 0),
                1 if destination in self.enemy_cells else 0,
                DIRECTION_RANK[direction],
            )

        candidates = sorted(DIRECTION_ORDER, key=score)
        return any(self._queue(unit, direction, reason) for direction in candidates)

    def flee(self, unit: Unit, threats: Iterable[Position], reason: str) -> bool:
        threat_cells = tuple(threats)
        candidates = sorted(
            DIRECTION_ORDER,
            key=lambda direction: (
                self.threat.get(_destination(unit.position, direction), 0),
                -min(
                    _distance(_destination(unit.position, direction), threat)
                    for threat in threat_cells
                ),
                self.memory.visited.get(_destination(unit.position, direction), 0),
                DIRECTION_RANK[direction],
            ),
        )
        return any(self._queue(unit, direction, reason) for direction in candidates)

    def flee_open(
        self,
        unit: Unit,
        threats: Iterable[Position],
        core_position: Position | None,
        reason: str,
        *,
        avoid: Iterable[Position] = (),
    ) -> bool:
        threat_cells = tuple(threats)
        avoid_cells = frozenset(avoid)

        def score(direction: Direction) -> tuple[int, int, int, int, int, int, int]:
            destination = _destination(unit.position, direction)
            nearby_obstacles = sum(
                1
                for obstacle in self.obstacles
                if _distance(destination, obstacle) <= 2
            )
            exits = sum(
                1
                for candidate_direction in DIRECTION_ORDER
                if self._can_enter(_destination(destination, candidate_direction))
            )
            threat_distance = (
                min(_distance(destination, threat) for threat in threat_cells)
                if threat_cells
                else 0
            )
            core_distance = (
                _distance(destination, core_position)
                if core_position is not None
                else 0
            )
            return (
                self.threat.get(destination, 0),
                nearby_obstacles,
                -exits,
                -threat_distance,
                -core_distance,
                self.memory.visited.get(destination, 0),
                DIRECTION_RANK[direction],
            )

        candidates = sorted(DIRECTION_ORDER, key=score)
        return any(
            self._queue(unit, direction, reason, avoid_cells)
            for direction in candidates
        )

    def flee_with_escort(
        self,
        unit: Unit,
        threats: Iterable[Position],
        escorts: Iterable[Unit],
        home: Position | None,
        reason: str,
    ) -> bool:
        threat_cells = tuple(threats)
        escort_positions = tuple(escort.position for escort in escorts)
        current_threat_distance = (
            min(_distance(unit.position, threat) for threat in threat_cells)
            if threat_cells
            else 0
        )

        def score(direction: Direction) -> tuple[int, ...]:
            destination = _destination(unit.position, direction)
            threat_distance = (
                min(_distance(destination, threat) for threat in threat_cells)
                if threat_cells
                else 0
            )
            escort_distances = tuple(
                _distance(destination, position) for position in escort_positions
            )
            nearby_escorts = sum(
                distance <= BEACON_GUARD_READY_RADIUS
                for distance in escort_distances
            )
            return (
                self.threat.get(destination, 0),
                1 if threat_distance < current_threat_distance else 0,
                -nearby_escorts,
                max(escort_distances, default=0),
                sum(escort_distances),
                _distance(destination, home) if home is not None else 0,
                -threat_distance,
                self.memory.visited.get(destination, 0),
                DIRECTION_RANK[direction],
            )

        candidates = sorted(DIRECTION_ORDER, key=score)
        return any(self._queue(unit, direction, reason) for direction in candidates)


class SmartTactic:
    def __init__(
        self,
        memory: TacticMemory | None = None,
        *,
        control_path: Path | None = None,
    ) -> None:
        self.memory = memory or TacticMemory()
        self.control_path = control_path or Path(
            os.environ.get("ARENA_HERO_CONTROL_FILE", CONTROL_FILENAME)
        )
        # 战况历史 / Core 轨迹 JSONL 路径（环境变量可覆盖；默认 None=不落盘，
        # 测试构造 SmartTactic() 不设 env 时零副作用）。
        raw_battle_path = os.environ.get("ARENA_HERO_BATTLE_HISTORY_FILE")
        self.battle_history_path = (
            Path(raw_battle_path) if raw_battle_path else None
        )
        raw_trail_path = os.environ.get("ARENA_HERO_CORE_TRAIL_FILE")
        self.core_trail_path = Path(raw_trail_path) if raw_trail_path else None
        # Ephemeral Tick plan: it is recomputed from the authoritative Turn and is
        # deliberately not persisted with long-lived route memory.
        self._lightning_plan: LightningPlan | None = None
        # 彗星本 tick 目标点（_comet_resolve_target 算出，供 _choose_*_lightning 分流用）。
        self._comet_target_tick: Position | None = None

    def choose_actions(self, turn: Turn) -> DecisionSummary:
        self.memory.load_control(self.control_path)
        # r 变更触发恒星模式迁移：把 core_target 设为新方形周界上离 Core 最近的角，
        # 走 star/march/fortify 转移状态机；到达后 _lightning_patrol_waypoint 自然
        # 接管新轨道。需在有 turn.core 的这里做（load_control 拿不到 Core 位置）。
        self._apply_pending_orbit_migration(turn)
        self.memory.refresh_recovery_target_hints()
        self.memory.refresh_browser_intel()
        self.memory.observe(turn)
        # 只在本 Tick 内协调多名游侠的覆盖格，不把未来 Tick 的动作带入。
        self.memory.current_shot_cells.clear()
        # 45°支援指派瞬态集每 tick 清空(防跨 tick 残留锁死指派)。
        self.memory.standoff_support_assigned.clear()
        # 冷却到期回收:过期游侠可重新被指派为支援。
        if self.memory.standoff_support_cooldown:
            for uid, until in list(self.memory.standoff_support_cooldown.items()):
                if turn.tick >= until:
                    self.memory.standoff_support_cooldown.pop(uid, None)
                    self.memory.standoff_support_stall.pop(uid, None)
        previous_events = Counter(event.event_type for event in turn.events)
        decisions = list(self.memory.observations)

        if turn.core is None:
            summary = self._summary(turn, previous_events, decisions)
            self._append_battle_history(turn)
            return summary

        planner = MovementPlanner(turn, self.memory, decisions)
        acted_units: set[UUID] = set()

        # Lightning is the only live branch.  Build one shared threat/medical/
        # funnel plan before actions so Units do not independently undo each other.
        self._lightning_plan = self._lightning_prepare_plan(turn, planner, decisions)
        # 哈雷彗星：每 tick 先刷新目标 + 维护编队（清伤员/补替补/守保留线）。
        # 决定本 tick 彗星成员该向哪个点推进；成员在 _choose_*_lightning 里被分流。
        self._comet_reinforce(turn)
        self._comet_target_tick = self._comet_resolve_target(turn)
        incoming_deposit = self._choose_workers(turn, planner, acted_units, decisions)
        self._choose_vanguards(turn, planner, acted_units, decisions)
        self._choose_rangers(turn, planner, acted_units, decisions)
        # Ranger MEDIVAC actions are already reserved above; generic healing serves
        # the remaining wounded units and patients that arrived at the Core.
        self._choose_healing(turn, planner, acted_units, decisions)
        self._choose_core(turn, planner, False, incoming_deposit, decisions)
        summary = self._summary(turn, previous_events, decisions)
        self._append_battle_history(turn)
        self._append_core_trail(turn)
        return summary

    def _record_enemy_unit_kill(
        self, kills: dict[str, int], target_id: str
    ) -> None:
        """把一次敌方单位击杀按兵种记进 kills 桶。

        兵种来源优先级：本 tick 冻结的 enemy_type_snapshot（observe 前快照，
        不受 sighting 清理影响）→ enemy_sightings（可能已被清，回退用）→ unknown。
        """
        unit_type = self.memory.enemy_type_snapshot.get(target_id)
        if unit_type is None:
            sighting = self.memory.enemy_sightings.get(target_id)
            unit_type = sighting.unit_type if sighting else None
        key = (
            f"enemy_{unit_type.lower()}"
            if unit_type and f"enemy_{unit_type.lower()}" in kills
            else "enemy_unknown"
        )
        kills[key] += 1

    def _append_battle_history(self, turn: Turn) -> None:
        """每 tick 追加一行战况增量到 arena_hero_battle_history.jsonl。

        滚动保留最近 BATTLE_HISTORY_MAX_LINES 行。路径为空时（测试）不落盘。
        """
        path = self.battle_history_path
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            kills = {
                "enemy_core": 0,
                "enemy_ranger": 0,
                "enemy_vanguard": 0,
                "enemy_worker": 0,
                "enemy_unknown": 0,
            }
            losses = {"worker": 0, "vanguard": 0, "ranger": 0}
            shots = {"hit": 0, "miss": 0}
            core_destroyed = False
            killed_this_tick: set[str] = set()
            for event in turn.events:
                event_type = event.event_type
                if event_type == "SHOT_HIT":
                    shots["hit"] += 1
                elif event_type == "SHOT_MISSED":
                    shots["miss"] += 1
                elif event_type == "CORE_DESTROYED":
                    core_destroyed = True
                if event_type != "UNIT_DAMAGED":
                    continue
                if event.values is None:
                    continue
                if int(event.values.get("hp", -1)) != 0:
                    continue  # 非致命伤害不记
                target_id = str(event.target_id) if event.target_id else None
                if target_id is None or target_id in killed_this_tick:
                    continue
                # 我方阵亡一律以 lightning_recent_deaths 为准（observe 用删除前后编制
                # 差集算，防 UNIT_DAMAGED 与 roster 移除不同步导致的漏计/双计）。
                # 这里只计敌方单位击杀：目标既不在本 tick 阵亡名单、也不在我方编制。
                if target_id in self.memory.lightning_recent_deaths:
                    killed_this_tick.add(target_id)
                    continue
                if target_id in self.memory.unit_labels:
                    killed_this_tick.add(target_id)
                    continue
                # 敌方单位被击杀：兵种从 enemy_type_snapshot 查（observe 前冻结，
                # 不受 sighting 清理影响），查不到记 unknown。
                self._record_enemy_unit_kill(kills, target_id)
                killed_this_tick.add(target_id)
            # 我方阵亡（observe 已算好的每 tick 阵亡，含全部阵亡渠道）
            for uid, object_type in self.memory.lightning_recent_deaths.items():
                key = object_type.lower()
                if key in losses:
                    losses[key] += 1
            # 敌方击杀去重：DESTRUCTION_PARTICIPATION 的 reason 为 CORE 或 UNIT，
            # target 未计过才 +1。CORE→enemy_core；UNIT→按兵种分桶。
            # 注意：服务器在敌方单位阵亡时只发 DESTRUCTION_PARTICIPATION(UNIT)，
            # 不发 fatal UNIT_DAMAGED，所以 UNIT 分支是敌方单位击杀的主来源。
            for event in turn.events:
                if event.event_type != "DESTRUCTION_PARTICIPATION":
                    continue
                target_id = str(event.target_id) if event.target_id else None
                if target_id is None:
                    continue
                if event.reason_code == "CORE":
                    if target_id in self.memory.battle_enemy_cores_seen:
                        continue
                    self.memory.battle_enemy_cores_seen.add(target_id)
                    kills["enemy_core"] += 1
                elif event.reason_code == "UNIT":
                    if target_id in killed_this_tick:
                        continue
                    if target_id in self.memory.battle_enemy_units_seen:
                        continue
                    # 我方单位不会进 DESTRUCTION_PARTICIPATION(UNIT)（那是敌方击杀我方
                    # 的事件，我方是受害方不是"我方击杀"）；但保守起见跳过我方编制。
                    if target_id in self.memory.unit_labels:
                        continue
                    self.memory.battle_enemy_units_seen.add(target_id)
                    self._record_enemy_unit_kill(kills, target_id)
                    killed_this_tick.add(target_id)
            if not any(kills.values()) and not any(losses.values()) and not any(
                shots.values()
            ) and not core_destroyed:
                return  # 无战斗事件，不落行
            record = {
                "tick": turn.tick,
                "ts": time.time(),
                "kills": kills,
                "losses": losses,
                "shots": shots,
                "core_destroyed": core_destroyed,
            }
            self._append_jsonl(path, record, BATTLE_HISTORY_MAX_LINES)
        except OSError:
            pass

    def _append_core_trail(self, turn: Turn) -> None:
        """每 tick 追加一行 Core 轨迹到 arena_hero_core_trail.jsonl。"""
        path = self.core_trail_path
        if path is None or turn.core is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            record = {
                "tick": turn.tick,
                "ts": time.time(),
                "pos": list(turn.core.position),
                "hp": turn.core.hp,
                "shield": turn.core.shield,
                "state": turn.core.view.state.value,
                "hold": self.memory.core_hold,
                "target": (
                    list(self.memory.core_target)
                    if self.memory.core_target is not None
                    else None
                ),
                "transfer_mode": self.memory.core_transfer_mode,
                "orbit_radius": self._lightning_patrol_radius(),
            }
            self._append_jsonl(path, record, CORE_TRAIL_MAX_LINES)
        except OSError:
            pass

    @staticmethod
    def _append_jsonl(path: Path, record: dict, max_lines: int) -> None:
        """以 O_APPEND 追加一行 JSON；文件超容量时截断（保留末尾 max_lines 行）。

        只在文件字节数超过阈值时做行数截断，避免每 tick O(n) 扫描。
        """
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        with path.open("a", encoding="utf-8") as stream:
            stream.write(line)
        try:
            if path.stat().st_size > max_lines * 128:
                lines = path.read_text(encoding="utf-8").splitlines()
                if len(lines) > max_lines:
                    path.write_text(
                        "\n".join(lines[-max_lines:]) + "\n", encoding="utf-8"
                    )
        except OSError:
            pass

    def _worker_requires_core_exit(
        self,
        turn: Turn,
        planner: MovementPlanner,
        worker: Worker,
    ) -> bool:
        core = turn.core
        if core is None or worker.position == core.position:
            return False
        if _distance(worker.position, core.position) != 1:
            return False
        core_adjacent = False
        non_core_exits = 0
        for direction in DIRECTION_ORDER:
            position = _destination(worker.position, direction)
            if position == core.position:
                if position not in planner.obstacles and position not in planner.enemy_cells:
                    core_adjacent = True
                continue
            if (
                position not in planner.obstacles
                and position not in planner.enemy_cells
                and self.memory.temporary_blocks.get(position, 0) <= turn.tick
                and planner.final_occupancy(position) < 2
            ):
                non_core_exits += 1
        return core_adjacent and non_core_exits == 0

    def _worker_toward(
        self,
        turn: Turn,
        planner: MovementPlanner,
        worker: Worker,
        goal: Position,
        reason: str,
    ) -> bool:
        if turn.core is None:
            avoid: tuple[Position, ...] = ()
        elif self._worker_requires_core_exit(turn, planner, worker):
            if any(other.cargo for other in turn.workers):
                return False
            avoid = ()
        else:
            avoid = (turn.core.position,)
        return planner.toward(worker, goal, reason, avoid=avoid)

    def _summary(
        self,
        turn: Turn,
        previous_events: Counter[str],
        decisions: list[str],
    ) -> DecisionSummary:
        plan = turn.plan
        return DecisionSummary(
            tick=turn.tick,
            unit_actions=len(plan.unit_actions),
            has_core_action=plan.core_action is not None,
            previous_events=dict(previous_events),
            resources=turn.resources,
            resource_capacity=turn.resource_capacity,
            population=len(turn.units),
            visible_enemies=len(turn.visible_enemies),
            decisions=tuple(decisions),
        )

    def _core_logistics_parking_target(
        self,
        turn: Turn,
        planner: MovementPlanner,
        unit: Unit,
    ) -> Position | None:
        """Choose a nearby parking cell without occupying a shelter doorway."""
        core = turn.core
        if core is None:
            return None
        corridor = _core_logistics_corridor(core.position, planner.obstacles)
        core_neighborhood = {core.position} | {
            _destination(core.position, direction) for direction in DIRECTION_ORDER
        }
        candidates: list[tuple[int, int, int, int, Position]] = []
        for radius in range(2, 9):
            for dx in range(-radius, radius + 1):
                dy = radius - abs(dx)
                for signed_dy in ({dy, -dy} if dy else {0}):
                    position = (
                        core.position[0] + dx,
                        core.position[1] + signed_dy,
                    )
                    if (
                        position in core_neighborhood
                        or position in corridor
                        or position in planner.obstacles
                        or position in planner.enemy_cells
                        or position in turn.resource_cells
                        or self.memory.temporary_blocks.get(position, 0) > turn.tick
                        or planner.final_occupancy(position) >= 2
                    ):
                        continue
                    candidates.append(
                        (
                            planner.threat.get(position, 0),
                            planner.final_occupancy(position),
                            _distance(unit.position, position),
                            self.memory.visited.get(position, 0),
                            position,
                        )
                    )
            if candidates:
                break
        return min(candidates)[-1] if candidates else None

    def _vacate_core_for_logistics(
        self,
        turn: Turn,
        planner: MovementPlanner,
        acted_units: set[UUID],
        decisions: list[str],
    ) -> None:
        core = turn.core
        if core is None or core.view.state is not CoreState.NORMAL:
            return
        if any(
            _distance(core.position, enemy.position) <= 5
            for enemy in turn.visible_enemies
        ):
            return
        trapped_workers = [
            worker
            for worker in turn.workers
            if (
                not worker.cargo
                and self._worker_requires_core_exit(turn, planner, worker)
            )
        ]
        near_cargo = any(
            worker.cargo and _distance(worker.position, core.position) <= 3
            for worker in turn.workers
        )
        owned_ids = {unit.id for unit in turn.units} | {core.id}
        owns_beacon = (
            turn.beacon.status is BeaconStatus.CARRIED
            and turn.beacon.carrier_id in owned_ids
        )
        shield_cap = 10 if owns_beacon else 5
        near_threat = any(
            _distance(core.position, enemy.position) <= 5
            for enemy in turn.visible_enemies
        )
        higher_priority_core_action = (
            turn.resources >= 1
            and (
                core.hp < 5
                or (
                    core.shield < shield_cap
                    and (near_threat or core.shield <= 2)
                )
            )
        )
        production_access_needed = (
            not higher_priority_core_action
            and self._select_spawn(turn, turn.resources) is not None
        )
        needs_core_space = (
            near_cargo
            or production_access_needed
            or bool(trapped_workers)
        )
        if not needs_core_space:
            return

        core_access_needed = near_cargo or bool(trapped_workers)
        priority_workers = [worker for worker in turn.workers if worker.cargo] + trapped_workers
        core_neighborhood = {core.position} | {
            _destination(core.position, direction) for direction in DIRECTION_ORDER
        }
        blockers = [
            unit
            for unit in turn.units
            if (
                unit.position == core.position
                or (core_access_needed and unit.position in core_neighborhood)
            )
            and unit.id not in acted_units
            and not (isinstance(unit, Worker) and unit.cargo)
            and unit.hp >= MAX_HP.get(unit.unit_type, 0)
        ]
        blockers.sort(
            key=lambda unit: (
                0 if unit.position == core.position else 1,
                min(
                    (
                        _distance(unit.position, worker.position)
                        for worker in priority_workers
                    ),
                    default=0,
                ),
                unit.id.bytes,
            )
        )
        vanguard_defenders: set[UUID] = set()
        ranger_defenders: set[UUID] = set()
        if False:
            vanguard_defenders, ranger_defenders = self._aggress_core_defender_ids(
                turn
            )
        defender_orders = {
            UnitType.VANGUARD: sorted(vanguard_defenders, key=lambda value: value.bytes),
            UnitType.RANGER: sorted(ranger_defenders, key=lambda value: value.bytes),
        }
        for blocker in blockers:
            strategic_goal = turn.beacon.position
            avoid_cells: tuple[Position, ...]
            defender_ids = defender_orders.get(blocker.unit_type, [])
            if blocker.id in defender_ids:
                offsets = (
                    AGGRESS_VANGUARD_WATCH_OFFSETS
                    if blocker.unit_type is UnitType.VANGUARD
                    else AGGRESS_RANGER_WATCH_OFFSETS
                )
                offset = offsets[defender_ids.index(blocker.id) % len(offsets)]
                strategic_goal = (
                    core.position[0] + offset[0],
                    core.position[1] + offset[1],
                )
            elif blocker.position in core_neighborhood:
                parking_target = self._core_logistics_parking_target(
                    turn,
                    planner,
                    blocker,
                )
                if parking_target is not None:
                    strategic_goal = parking_target
            if (
                isinstance(blocker, Worker)
                and not blocker.cargo
                and blocker.position == core.position
                and trapped_workers
                and not near_cargo
            ):
                egress_candidates: list[tuple[int, int, int, Position]] = []
                for direction in DIRECTION_ORDER:
                    position = _destination(core.position, direction)
                    if (
                        position in planner.obstacles
                        or position in planner.enemy_cells
                        or self.memory.temporary_blocks.get(position, 0) > turn.tick
                        or planner.final_occupancy(position) >= 2
                    ):
                        continue
                    onward_open = sum(
                        1
                        for onward_direction in DIRECTION_ORDER
                        if (onward := _destination(position, onward_direction))
                        != core.position
                        and onward not in planner.obstacles
                        and onward not in planner.enemy_cells
                        and self.memory.temporary_blocks.get(onward, 0) <= turn.tick
                        and planner.final_occupancy(onward) < 2
                    )
                    egress_candidates.append(
                        (
                            -onward_open,
                            planner.threat.get(position, 0),
                            DIRECTION_RANK[direction],
                            position,
                        )
                    )
                if egress_candidates:
                    strategic_goal = min(egress_candidates)[-1]
            if strategic_goal == core.position:
                direction = self.memory.core_heading or Direction.UP
                dx, dy = direction.delta
                strategic_goal = (core.position[0] + dx * 3, core.position[1] + dy * 3)
            # 地形可能把空载工人封在 core 邻格。带货工人尚未进入门口时，
            # 允许它先穿过空闲的 core 格，下一回合再把它疏散出去。
            core_door_escape = False
            if (
                isinstance(blocker, Worker)
                and not blocker.cargo
                and core_access_needed
                and not near_cargo
                and blocker.position != core.position
                and planner.final_occupancy(core.position) < 2
                and core.position not in planner.obstacles
                and core.position not in planner.enemy_cells
                and self.memory.temporary_blocks.get(core.position, 0) <= turn.tick
            ):
                non_core_exit = any(
                    position != core.position
                    and position not in planner.obstacles
                    and position not in planner.enemy_cells
                    and self.memory.temporary_blocks.get(position, 0) <= turn.tick
                    and planner.final_occupancy(position) < 2
                    for direction in DIRECTION_ORDER
                    for position in (_destination(blocker.position, direction),)
                )
                core_door_escape = not non_core_exit
            if core_door_escape:
                avoid_cells = tuple(
                    position
                    for position in core_neighborhood
                    if position != core.position
                )
                decisions.append(
                    f"worker:{_short_id(blocker.id)} core_door_escape"
                )
            elif core_access_needed and blocker.position != core.position:
                # 已经退到 core 邻格的挡路单位，下一步必须继续走出门口，
                # 否则会在邻格与 core 之间反复横跳，持续卡住回仓工人。
                avoid_cells = tuple(core_neighborhood)
            else:
                avoid_cells = (core.position,)
            escape_origin = blocker.position if core_door_escape else None
            if planner.toward(
                blocker,
                strategic_goal,
                "vacate_core_for_logistics",
                avoid=avoid_cells,
            ):
                acted_units.add(blocker.id)
                if escape_origin is not None:
                    # When a trapped worker has to cross the Core to escape,
                    # keep its former doorway cell briefly reserved. Without
                    # this short cooldown the next Tick can immediately route
                    # it back to the same neighbor, producing a visible
                    # Core↔door oscillation and starving cargo deposits.
                    self.memory.temporary_blocks[escape_origin] = max(
                        self.memory.temporary_blocks.get(escape_origin, 0),
                        turn.tick + 3,
                    )
                    if core_door_escape:
                        # Once the worker has crossed the Core, reserve the
                        # Core cell briefly as well. Otherwise the next
                        # planner pass can choose the Core as its only escape
                        # route again and oscillate through the doorway.
                        self.memory.temporary_blocks[core.position] = max(
                            self.memory.temporary_blocks.get(core.position, 0),
                            turn.tick + 3,
                        )
                # Keep the newly freed Core slot available for production this
                # Tick. Without this reservation, another worker can route
                # back onto the Core immediately, leaving final occupancy full
                # and making the producer wait forever.
                should_reserve_core = (
                    blocker.position == core.position
                    and not near_cargo
                    and (
                        isinstance(blocker, Worker)
                        or turn.resources >= unit_cost(UnitType.WORKER, len(turn.units))
                    )
                )
                if should_reserve_core:
                    self.memory.temporary_blocks[core.position] = max(
                        self.memory.temporary_blocks.get(core.position, 0),
                        # A worker leaving the Core needs a few ticks of door
                        # priority so the adjacent guard ring can open a real
                        # exit; otherwise another worker re-enters immediately
                        # and the same door cycle repeats.
                        turn.tick + (4 if isinstance(blocker, Worker) else 1),
                    )
                    decisions.append("core_spawn_slot_reserved")
                if isinstance(blocker, Worker):
                    self.memory.clear_worker_goal(blocker)
                decisions.append(
                    f"core_logistics_space vacated_by="
                    f"{blocker.unit_type.value.lower()}:{_short_id(blocker.id)}"
                )
                return

    def _choose_beacon(
        self,
        turn: Turn,
        planner: MovementPlanner,
        acted_units: set[UUID],
        decisions: list[str],
    ) -> bool:
        owned_ids = {unit.id for unit in turn.units}
        if turn.core is not None:
            owned_ids.add(turn.core.id)
        if (
            turn.beacon.status is BeaconStatus.CARRIED
            and turn.beacon.carrier_id in owned_ids
        ):
            if turn.core is not None and turn.beacon.carrier_id == turn.core.id:
                if turn.core.view.state is CoreState.MOVING:
                    turn.core.cancel_move()
                    decisions.append(
                        "core cancel_move reason=core_beacon_forbidden"
                    )
                    self.memory.decision_totals[
                        "core:cancel_move_beacon_forbidden"
                    ] += 1
                else:
                    turn.core.drop_beacon()
                    decisions.append(
                        "core drop_beacon reason=core_beacon_forbidden"
                    )
                    self.memory.decision_totals[
                        "core:drop_beacon_forbidden"
                    ] += 1
                return True
            return False

        if turn.beacon.status is BeaconStatus.GROUND:
            home_vanguards, home_rangers = self._beacon_home_reserve_ids(turn)
            candidates = [
                unit
                for unit in turn.units
                if unit.position == turn.beacon.position
                and unit.id not in home_vanguards
                and unit.id not in home_rangers
            ]
            candidates.sort(
                key=lambda unit: (
                    0 if isinstance(unit, Vanguard) else 1 if isinstance(unit, Ranger) else 2,
                    unit.id.bytes,
                )
            )
            if candidates:
                carrier = candidates[0]
                carrier.pickup_beacon()
                acted_units.add(carrier.id)
                decisions.append(
                    f"{carrier.view.unit_type.value.lower()}:{_short_id(carrier.id)} "
                    "pickup_beacon reason=standing_on_beacon"
                )
                self.memory.decision_totals["unit:pickup_beacon"] += 1
                return False

        if turn.core is None or any(
            _distance(turn.core.position, enemy.position) <= 5
            for enemy in turn.visible_enemies
        ):
            return False

        home_vanguards, home_rangers = self._beacon_home_reserve_ids(turn)
        candidates: list[Unit] = [
            unit for unit in turn.vanguards if unit.id not in home_vanguards
        ]
        if len(turn.rangers) > 1:
            candidates.extend(
                unit for unit in turn.rangers if unit.id not in home_rangers
            )
        develop_needs_resource_search = (
            False
            and (
                bool(self.memory.browser_resource_hints)
                or (
                    not turn.resource_cells
                    and not self.memory.resource_last_seen
                    and not self.memory.recovery_targets
                )
            )
        )
        if len(turn.workers) > 4 and not develop_needs_resource_search:
            candidates.extend(worker for worker in turn.workers if not worker.cargo)
        if not candidates:
            return False
        pursuer = min(candidates, key=lambda unit: (_distance(unit.position, turn.beacon.position), unit.id.bytes))
        if _distance(pursuer.position, turn.beacon.position) <= 24:
            if planner.toward(pursuer, turn.beacon.position, "beacon_pursuit"):
                acted_units.add(pursuer.id)
        return False

    def _core_target_arrived(self, turn: Turn) -> bool:
        """Core 是否已到达网页控制台设定的目标坐标（进入到达死区）。"""
        core = turn.core
        target = self.memory.core_target
        if core is None or target is None:
            return False
        return _distance(core.position, target) <= CORE_BEACON_HYSTERESIS

    def _core_target_reached(self, turn: Turn) -> bool:
        """Core 是否"到达"用户目标——满足以下任一：

        1. Manhattan 距离 ≤ CORE_BEACON_HYSTERESIS（标准到达死区）。
        2. 目标格本身是障碍物（或其近邻被障碍占满），Core 已贴到目标周围
           最近的可占格——目标点不可站时，"贴到旁边"即视为迁移成功。
        """
        core = turn.core
        target = self.memory.core_target
        if core is None or target is None:
            return False
        if _distance(core.position, target) <= CORE_BEACON_HYSTERESIS:
            return True
        # 目标格是障碍物 → Core 贴到目标 8 邻域内即算到达。
        obstacles = set(self.memory.known_obstacles) | set(turn.obstacle_cells)
        if target not in obstacles:
            return False
        return _distance(core.position, target) <= CORE_BEACON_HYSTERESIS + 1

    def _march_active(self, turn: Turn) -> bool:
        """急行军转移中：有目标、未到达、且模式为 march。"""
        return (
            self.memory.core_transfer_mode == "march"
            and self.memory.core_target is not None
            and not self._core_target_arrived(turn)
        )

    def _fortify_hold_active(self, turn: Turn) -> bool:
        """坚壁清野转移中：有目标、未到达、且模式为 fortify（工人只采不交）。"""
        return (
            self.memory.core_transfer_mode == "fortify"
            and self.memory.core_target is not None
            and not self._core_target_arrived(turn)
        )

    def _worker_combat_contacts(
        self,
        turn: Turn,
        planner: MovementPlanner,
        worker: Worker,
    ) -> tuple[UnitView, ...]:
        """返回该工人已经接触到的敌方攻击单位（工人/Core 不算）。"""
        return tuple(
            enemy
            for enemy in turn.visible_enemies
            if isinstance(enemy, UnitView)
            and enemy.unit_type in {UnitType.RANGER, UnitType.VANGUARD}
            and (
                planner.threat.get(worker.position, 0) > 0
                or _unit_can_see_position(worker, enemy.position, planner.obstacles)
            )
        )

    def _evacuate_worker_combat_contacts(
        self,
        turn: Turn,
        planner: MovementPlanner,
        acted_units: set[UUID],
        decisions: list[str],
    ) -> None:
        """在漏斗、货运和采集之前抢占工人的战斗撤离动作。"""
        for worker in sorted(turn.workers, key=_uuid_key):
            if worker.id in acted_units:
                continue
            contacts = self._worker_combat_contacts(turn, planner, worker)
            if not contacts:
                continue
            self.memory.clear_worker_goal(worker)
            if planner.flee_from_combat(
                worker,
                contacts,
                turn.core.position if turn.core is not None else None,
                "worker_combat_flee",
            ):
                decisions.append(
                    f"worker:{_short_id(worker.id)} combat_flee "
                    f"enemies={','.join(_short_id(enemy.id) for enemy in contacts)}"
                )
                self.memory.decision_totals["worker:combat_flee"] += 1
            else:
                worker.wait()
                decisions.append(
                    f"worker:{_short_id(worker.id)} combat_flee_wait "
                    f"enemies={','.join(_short_id(enemy.id) for enemy in contacts)}"
                )
                self.memory.decision_totals["worker:combat_flee_wait"] += 1
            acted_units.add(worker.id)

    def _choose_workers(
        self,
        turn: Turn,
        planner: MovementPlanner,
        acted_units: set[UUID],
        decisions: list[str],
    ) -> int:
        assert turn.core is not None
        incoming_deposit = 0
        remaining_space = turn.resource_space
        empty_workers: list[Worker] = []
        owns_beacon = _owns_beacon(turn)
        resource_target_core_leash = None
        if not owns_beacon:
            if False:
                resource_target_core_leash = (
                    DEVELOP_RESOURCE_TARGET_CORE_LEASH_DISTANCE
                )
            elif False:
                resource_target_core_leash = (
                    BEACON_RESOURCE_TARGET_CORE_LEASH_DISTANCE
                )
        return_position = (
            turn.core.view.destination
            if turn.core.view.state is CoreState.MOVING
            and turn.core.view.destination is not None
            else turn.core.position
        )

        # 战斗撤离必须比漏斗阻挡更早。否则被漏斗选中的工人会先被标记
        # acted，随后跳过逃跑，再次出现“看见敌军还往火力里钻”的回归。
        self._evacuate_worker_combat_contacts(
            turn, planner, acted_units, decisions
        )

        # 安全工人才允许承担漏斗阻挡。
        self._lightning_execute_funnel_workers(turn, planner, acted_units, decisions)

        for worker in sorted(turn.workers, key=_uuid_key):
            if worker.id in acted_units:
                continue

            if worker.cargo:
                self.memory.clear_worker_goal(worker)
                if self._fortify_hold_active(turn):
                    # 坚壁清野：携带资源但不提交，回到自己的轨道带货等待；
                    # Core 到达目标后（_fortify_hold_active 变 False）恢复交付。
                    orbit = self._lightning_orbit_waypoint(
                        turn, worker, UnitType.WORKER
                    )
                    if orbit is not None and not self._lightning_step_toward(
                        turn,
                        planner,
                        worker,
                        orbit,
                        "worker_fortify_hold",
                    ):
                        worker.wait()
                    decisions.append(
                        f"worker:{_short_id(worker.id)} fortify_hold cargo={worker.cargo}"
                    )
                    self.memory.decision_totals["worker:fortify_hold"] += 1
                    acted_units.add(worker.id)
                    continue
                if worker.position == turn.core.position:
                    if turn.core.view.state is CoreState.NORMAL and remaining_space > 0:
                        worker.deposit()
                        deposited = min(worker.cargo, remaining_space)
                        remaining_space -= deposited
                        incoming_deposit += deposited
                        decisions.append(
                            f"worker:{_short_id(worker.id)} deposit expected={deposited}"
                        )
                        self.memory.decision_totals["worker:deposit"] += 1
                    elif worker.position != return_position:
                        planner.toward(
                            worker,
                            return_position,
                            "rendezvous_moving_core",
                        )
                    continue
                if (
                    turn.core.view.state is CoreState.NORMAL
                    and _distance(worker.position, return_position) <= 2
                    and (
                        planner.final_occupancy(return_position) >= 2
                        or remaining_space <= 0
                    )
                ):
                    # 邻近 core 的 cargo 工人优先排队等待，避免为了抢入口
                    # 在 core 周围来回走位，把物流效率进一步拖慢。
                    decisions.append(
                        f"worker:{_short_id(worker.id)} cargo_queue_hold "
                        f"pos={worker.position} core={return_position}"
                    )
                    self.memory.decision_totals["worker:cargo_queue_hold"] += 1
                    continue
                if worker.position != return_position:
                    planner.toward(worker, return_position, "return_cargo")
                continue

            if self._march_active(turn):
                # 急行军：停止采集，回归自己的轨道跟随 Core 稳定推进。
                self.memory.clear_worker_goal(worker)
                orbit = self._lightning_orbit_waypoint(
                    turn, worker, UnitType.WORKER
                )
                if orbit is not None and not self._lightning_step_toward(
                    turn,
                    planner,
                    worker,
                    orbit,
                    "worker_march",
                ):
                    worker.wait()
                decisions.append(
                    f"worker:{_short_id(worker.id)} march orbit={orbit}"
                )
                self.memory.decision_totals["worker:march"] += 1
                acted_units.add(worker.id)
                continue
            empty_workers.append(worker)

        unassigned = {worker.id: worker for worker in empty_workers}
        if False:
            # Beacon expeditions are combat-only.  Retire legacy Worker beacon
            # goals so the economy stays around the Core while the escort leaves.
            for worker in unassigned.values():
                goal = self.memory.worker_goals.get(str(worker.id))
                if goal is None or goal.kind != "beacon":
                    continue
                self.memory.clear_worker_goal(worker)
                decisions.append(
                    f"worker:{_short_id(worker.id)} beacon_economy_recall"
                )
                self.memory.decision_totals["worker:beacon_economy_recall"] += 1
        # 迷路检测：有移动目标但无法到达 → 清除目标重新分配
        # 两种模式：①位置完全不动 ②来回震荡（打转，位置在 2-3 格间反复）
        stuck_cleared = 0
        for worker_id, worker in list(unassigned.items()):
            goal = self.memory.worker_goals.get(str(worker.id))
            if goal is None or goal.position == worker.position:
                continue
            uid = str(worker.id)
            last_moved = self.memory.last_position_tick.get(uid, turn.tick)
            stationary = turn.tick - last_moved > STUCK_TICKS
            recent = self.memory.recent_positions.get(uid, [])
            spinning = (
                len(recent) >= STUCK_TICKS
                and len(set(recent)) <= SPIN_POSITION_BUDGET
                and turn.tick - goal.created_tick >= STUCK_TICKS // 2
            )
            if stationary or spinning:
                reason = "stationary" if stationary else "spinning"
                if goal.kind in {
                    "frontier",
                    "develop_frontier",
                    "resource_sweep",
                    "refilled_chunk",
                    "visible_resource",
                    "browser_resource_hint",
                }:
                    self.memory.temporary_blocks[goal.position] = max(
                        self.memory.temporary_blocks.get(goal.position, 0),
                        turn.tick + STUCK_TICKS,
                    )
                    decisions.append(
                        f"worker:{_short_id(worker.id)} stuck_target_blocked "
                        f"target={goal.position} until={turn.tick + STUCK_TICKS}"
                    )
                self.memory.clear_worker_goal(worker)
                decisions.append(
                    f"worker:{_short_id(worker.id)} stuck_clear reason={reason} "
                    f"goal={goal.position} unique_cells={len(set(recent))}"
                )
                self.memory.decision_totals["worker:stuck_clear"] += 1
                stuck_cleared += 1
        if stuck_cleared:
            decisions.append(f"worker_stuck_cleared count={stuck_cleared}")
        if False and not owns_beacon:
            resource_signals = set(turn.resource_cells) | set(
                self.memory.resource_last_seen
            )
            nearby_resource_signal = any(
                _distance(turn.core.position, position)
                < DEVELOP_RESOURCE_TARGET_CORE_LEASH_DISTANCE
                or any(
                    _distance(worker.position, position) <= 3
                    for worker in unassigned.values()
                )
                for position in resource_signals
            )
            search_void = (
                not nearby_resource_signal
                and not self.memory.recovery_targets
                and not self.memory.browser_resource_hints
            )
            for worker_id, worker in list(unassigned.items()):
                recall_goal = self.memory.worker_goals.get(str(worker.id))
                existing_recall = (
                    recall_goal is not None
                    and recall_goal.kind == "develop_local_recall"
                    and recall_goal.position == turn.core.position
                )
                if existing_recall and any(
                    _distance(worker.position, position) <= 2
                    for position in turn.resource_cells
                ):
                    # A safe remote Worker that is already next to a visible
                    # node can finish this harvest before resuming recall.
                    # This avoids sending a local Worker across the map for a
                    # resource the returning Worker can collect immediately.
                    continue
                outside_local_area = (
                    _distance(worker.position, turn.core.position)
                    > DEVELOP_WIDE_SEARCH_MAX_RADIUS
                )
                if not outside_local_area:
                    if existing_recall:
                        self.memory.clear_worker_goal(worker)
                    continue
                if not existing_recall and not search_void:
                    continue
                if not existing_recall:
                    self.memory.set_worker_goal(
                        worker,
                        "develop_local_recall",
                        turn.core.position,
                        turn.tick,
                    )
                recent_avoid = frozenset(
                    position
                    for position in self.memory.recent_positions.get(
                        str(worker.id), []
                    )[-4:]
                    if position != worker.position
                    and position != turn.core.position
                )
                moved = planner.toward(
                    worker,
                    turn.core.position,
                    "develop_local_recall",
                    avoid=recent_avoid,
                )
                if not moved and recent_avoid:
                    # 狭窄通道里唯一可行步可能正是上一格；避让失败时允许
                    # 一步回退，避免把回仓路线锁死在当前位置。
                    moved = planner.toward(
                        worker,
                        turn.core.position,
                        "develop_local_recall:backtrack",
                    )
                if moved:
                    unassigned.pop(worker_id, None)
                    decisions.append(
                        f"worker:{_short_id(worker.id)} local_recall "
                        f"distance={_distance(worker.position, turn.core.position)}"
                    )
                    self.memory.decision_totals["worker:develop_local_recall"] += 1
                else:
                    unassigned.pop(worker_id, None)
        # cargo 工人回程打转检测：return_cargo 不走 worker_goals，stuck 检测覆盖不到
        for worker in turn.workers:
            if worker.id in acted_units or not worker.cargo:
                continue
            if _distance(worker.position, return_position) <= 4:
                continue
            recent = self.memory.recent_positions.get(str(worker.id), [])
            if (
                len(recent) >= STUCK_TICKS
                and len(set(recent)) <= SPIN_POSITION_BUDGET
            ):
                decisions.append(
                    f"worker:{_short_id(worker.id)} cargo_stuck "
                    f"pos={worker.position} core={return_position}"
                )
                self.memory.decision_totals["worker:cargo_stuck"] += 1
        harvested_cells: set[Position] = set()
        for position in sorted(turn.resource_cells):
            contenders = sorted(
                (
                    worker
                    for worker in empty_workers
                    if worker.position == position
                ),
                key=_uuid_key,
            )
            if not contenders:
                continue
            winner = contenders[0]
            winner.harvest()
            acted_units.add(winner.id)
            unassigned.pop(winner.id, None)
            harvested_cells.add(position)
            self.memory.clear_worker_goal(winner)
            decisions.append(f"worker:{_short_id(winner.id)} harvest at={position}")
            self.memory.decision_totals["worker:harvest"] += 1

        self._trim_refilled_chunk_goals(turn, unassigned, decisions)
        available_resources = set(turn.resource_cells) - harvested_cells
        if resource_target_core_leash is not None:
            # A recalled remote Worker is intentionally absent from
            # `unassigned`; do not let its nearby resource keep a local Worker
            # on a doomed cross-map route. This also prevents a combat
            # expedition's vision from exporting Beacon-mode Workers.
            nearby_workers = tuple(unassigned.values())
            far_resources = {
                position
                for position in available_resources
                if (
                    _distance(turn.core.position, position)
                    >= resource_target_core_leash
                    and not any(
                        _distance(worker.position, position) <= 3
                        for worker in nearby_workers
                    )
                )
            }
            if far_resources:
                available_resources.difference_update(far_resources)
                decisions.append(
                    f"resource_leash_trimmed mode={self.memory.mode} "
                    f"count={len(far_resources)}"
                )
                self.memory.decision_totals[
                    f"resource:{self.memory.mode}_leash_trimmed"
                ] += len(far_resources)
        resource_signal_available = bool(available_resources)
        actionable_resource_memory = set(self.memory.resource_last_seen)
        if resource_target_core_leash is not None:
            actionable_resource_memory = {
                position
                for position in actionable_resource_memory
                if (
                    _distance(turn.core.position, position)
                    < resource_target_core_leash
                    or any(
                        _distance(worker.position, position) <= 3
                        for worker in unassigned.values()
                    )
                )
            }
        reserved_targets: set[Position] = set()

        full_capacity = turn.resources >= turn.resource_capacity
        # Each exact resource signal can productively occupy only one Worker.
        # Keep surplus Workers on the wide sweep instead of collapsing the
        # whole economy to the 5/8/11-cell rings while one Worker validates a
        # remembered or currently visible node.
        exact_resource_tasks = len(
            set(available_resources) | actionable_resource_memory
        )
        reserved_scout_tasks = int(bool(self.memory.recovery_targets))
        if (
            False
            and self.memory.browser_resource_hints
        ):
            reserved_scout_tasks += BROWSER_RESOURCE_SCOUT_LIMIT
        productive_worker_slots = exact_resource_tasks + reserved_scout_tasks
        resource_sweep_active = (
            False
            and not full_capacity
            and productive_worker_slots < len(unassigned)
        )
        develop_wide_search = (
            False
            and not full_capacity
            and not resource_signal_available
            and not actionable_resource_memory
            and not self.memory.recovery_targets
            and not self.memory.browser_resource_hints
        )
        wide_resource_search = develop_wide_search or resource_sweep_active
        if wide_resource_search:
            for worker in unassigned.values():
                goal = self.memory.worker_goals.get(str(worker.id))
                if goal is not None and goal.kind not in {
                    "develop_frontier",
                    "resource_sweep",
                    "refilled_chunk",
                    "visible_resource",
                    "last_seen_resource",
                    "resource_recovery",
                    "browser_resource_hint",
                }:
                    self.memory.clear_worker_goal(worker)

        # Manual recovery/scout coordinates reserve at most one worker before
        # normal resource assignments; the remaining workers keep harvesting.
        self._assign_recovery_target(
            turn,
            planner,
            unassigned,
            reserved_targets,
            decisions,
        )

        # Keep a still-visible resource assignment stable instead of switching
        # to whichever point happens to be one step closer on this Tick.
        for worker_id, worker in list(unassigned.items()):
            goal = self.memory.worker_goals.get(str(worker.id))
            if (
                goal is not None
                and goal.kind == "visible_resource"
                and resource_target_core_leash is not None
                and _distance(turn.core.position, goal.position)
                >= resource_target_core_leash
                and _distance(worker.position, goal.position) > 3
            ):
                self.memory.clear_worker_goal(worker)
                decisions.append(
                    f"worker:{_short_id(worker.id)} resource_leash_trim "
                    f"goal={goal.position}"
                )
                self.memory.decision_totals[
                    "worker:resource_leash_trim"
                ] += 1
                goal = None
            if (
                goal is None
                or goal.position not in available_resources
                or goal.position in reserved_targets
            ):
                continue

            # === 动态切换到更近资源：途中发现近 2 格以上的资源立即切换 ===
            # 避免"路过近资源走向远资源"，提升采集效率。
            current_distance = _distance(worker.position, goal.position)
            switch_threshold = 2  # 至少近 2 格才切换，避免频繁摇摆
            closer_resources = [
                pos for pos in available_resources
                if pos != goal.position
                and _distance(worker.position, pos) < current_distance - switch_threshold
            ]

            if closer_resources:
                # 找出最近的资源
                new_target = min(closer_resources, key=lambda pos: _distance(worker.position, pos))
                new_distance = _distance(worker.position, new_target)
                # 释放旧目标，切换到新目标
                available_resources.add(goal.position)  # 旧目标重新可用
                available_resources.discard(new_target)
                self.memory.set_worker_goal(worker, "visible_resource", new_target, turn.tick)
                decisions.append(
                    f"worker:{_short_id(worker.id)} switch_to_closer_resource "
                    f"old={goal.position}(d={current_distance}) new={new_target}(d={new_distance})"
                )
                self.memory.decision_totals["worker:switch_to_closer"] += 1
                goal = self.memory.worker_goals.get(str(worker.id))  # 更新 goal 引用

            self.memory.set_worker_goal(worker, "visible_resource", goal.position, goal.created_tick)
            if self._worker_toward(
                turn,
                planner,
                worker,
                goal.position,
                "visible_resource:continue",
            ):
                reserved_targets.add(goal.position)
                available_resources.discard(goal.position)
                unassigned.pop(worker_id, None)

        # A freshly visible node is current truth, so it preempts a patrol or
        # old chunk probe immediately.  Otherwise a Worker may walk past a
        # resource in its own view until that stale exploration goal expires.
        if available_resources:
            for worker in unassigned.values():
                goal = self.memory.worker_goals.get(str(worker.id))
                if goal is not None and goal.kind in {
                    "frontier",
                    "develop_frontier",
                    "resource_sweep",
                    "refilled_chunk",
                }:
                    self.memory.clear_worker_goal(worker)

        # A resource that leaves current vision remains a confirmed stale hint.
        # Keep its assigned Worker on course until that exact cell is visible
        # and absent, harvested, or explicitly overridden by Manual control.
        for worker_id, worker in list(unassigned.items()):
            goal = self.memory.worker_goals.get(str(worker.id))
            if (
                goal is None
                or goal.kind != "visible_resource"
                or goal.position in available_resources
                or goal.position in reserved_targets
            ):
                continue
            reserved_targets.add(goal.position)
            self._worker_toward(
                turn,
                planner,
                worker,
                goal.position,
                "visible_resource:fog_continue",
            )
            unassigned.pop(worker_id, None)

        self._assign_worker_targets(
            turn,
            planner,
            unassigned,
            available_resources,
            reserved_targets,
            kind="visible_resource",
        )

        for worker_id, worker in list(unassigned.items()):
            goal = self.memory.worker_goals.get(str(worker.id))
            if goal is None or goal.position in reserved_targets:
                continue
            # 探索/低可信资源目标抵达后立即换点。此前目标会保留到过期，
            # 工人站在原地空转，导致没有可见资源时资源收入完全停滞。
            if (
                goal.position == worker.position
                and goal.kind
                in {
                    "frontier",
                    "develop_frontier",
                    "resource_sweep",
                    "browser_resource_hint",
                }
            ):
                self.memory.clear_worker_goal(worker)
                decisions.append(
                    f"worker:{_short_id(worker.id)} goal_reached_rotate "
                    f"kind={goal.kind} position={goal.position}"
                )
                self.memory.decision_totals["worker:goal_reached_rotate"] += 1
                continue
            if (
                goal.kind == "browser_resource_hint"
                and goal.position not in self.memory.browser_resource_hints
            ):
                self.memory.clear_worker_goal(worker)
                continue
            if (
                goal.kind == "frontier"
                and not owns_beacon
                and _distance(goal.position, turn.beacon.position)
                > _distance(turn.core.position, turn.beacon.position)
                + FRONTIER_BEACON_BACKTRACK_TOLERANCE
            ):
                self.memory.clear_worker_goal(worker)
                continue
            if (
                goal.kind == "last_seen_resource"
                and not owns_beacon
                and not _last_seen_resource_allowed(
                    worker.position,
                    goal.position,
                    turn.beacon.position,
                )
            ):
                self.memory.clear_worker_goal(worker)
                decisions.append(
                    f"last_seen_resource_strategic_trimmed "
                    f"worker={_short_id(worker.id)} goal={goal.position}"
                )
                self.memory.decision_totals[
                    "last_seen_resource:strategic_trimmed"
                ] += 1
                continue
            if (
                goal.kind in {"frontier", "develop_frontier", "resource_sweep"}
                and turn.tick - goal.created_tick > 24
            ):
                self.memory.clear_worker_goal(worker)
                continue
            if goal.kind == "resource_sweep":
                search_leash = (
                    BEACON_RESOURCE_SWEEP_MAX_RADIUS
                    if False
                    else AGGRESS_RESOURCE_SWEEP_MAX_RADIUS
                )
            else:
                search_leash = DEVELOP_WIDE_SEARCH_MAX_RADIUS
            if (
                goal.kind in {"resource_sweep", "develop_frontier"}
                and _distance(turn.core.position, goal.position) > search_leash
            ):
                # Drop legacy wide-search waypoints immediately after a reload;
                # their long walks delay the next harvest and deposit cycle.
                self.memory.clear_worker_goal(worker)
                decisions.append(
                    f"worker:{_short_id(worker.id)} local_search_trim "
                    f"kind={goal.kind} goal={goal.position} leash={search_leash}"
                )
                self.memory.decision_totals["worker:local_search_trim"] += 1
                continue
            reserved_targets.add(goal.position)
            if goal.kind in {"develop_frontier", "resource_sweep"}:
                self.memory.worker_search_radius[str(worker.id)] = max(
                    self.memory.worker_search_radius.get(str(worker.id), 0),
                    _distance(turn.core.position, goal.position),
                )
            if self._worker_toward(
                turn,
                planner,
                worker,
                goal.position,
                goal.kind,
            ):
                unassigned.pop(worker_id, None)
                if goal.kind == "develop_frontier":
                    self.memory.decision_totals["worker:develop_explore"] += 1
                elif goal.kind == "resource_sweep":
                    self.memory.decision_totals["worker:resource_sweep"] += 1

        remembered_resources = {
            position
            for position, seen_tick in self.memory.resource_last_seen.items()
            if position not in turn.resource_cells
            and position not in reserved_targets
            and turn.tick - seen_tick <= 12
            and (
                True
                or owns_beacon
                or position in actionable_resource_memory
            )
            and (
                owns_beacon
                or _last_seen_resource_allowed(
                    turn.core.position,
                    position,
                    turn.beacon.position,
                )
            )
        }
        self._assign_worker_targets(
            turn,
            planner,
            unassigned,
            remembered_resources,
            reserved_targets,
            kind="last_seen_resource",
        )

        if False and not full_capacity:
            browser_targets = {
                position
                for position in self.memory.browser_resource_hints
                if position not in turn.resource_cells
                and position not in self.memory.resource_last_seen
                and position not in reserved_targets
                and _distance(turn.core.position, position)
                <= BROWSER_RESOURCE_HINT_MAX_DISTANCE
                and not _currently_visible(turn, position, self.memory.known_obstacles)
            }
            # 远程提示只派一个试探工人，其余工人保持本地搜索和采集编队。
            browser_workers = sorted(
                unassigned.values(),
                key=lambda worker: (
                    min(
                        (_distance(worker.position, target) for target in browser_targets),
                        default=0,
                    ),
                    worker.id.bytes,
                ),
            )[:BROWSER_RESOURCE_SCOUT_LIMIT]
            browser_unassigned = {
                worker.id: worker
                for worker in browser_workers
            }
            candidate_ids = set(browser_unassigned)
            self._assign_worker_targets(
                turn,
                planner,
                browser_unassigned,
                browser_targets,
                reserved_targets,
                kind="browser_resource_hint",
            )
            assigned_ids = candidate_ids - set(browser_unassigned)
            for worker_id in assigned_ids:
                unassigned.pop(worker_id, None)
            assigned = len(assigned_ids)
            if assigned:
                decisions.append(
                    f"browser_resource_assigned workers={assigned} "
                    f"hints={len(browser_targets)}"
                )
                self.memory.decision_totals["worker:browser_resource_hint"] += assigned

        if not full_capacity:
            self._assign_refilled_chunks(
                turn,
                planner,
                unassigned,
                reserved_targets,
            )

        if False:
            for worker_id, worker in list(unassigned.items()):
                self.memory.clear_worker_goal(worker)
                if (
                    _distance(worker.position, turn.core.position) > 4
                    and self._worker_toward(
                        turn,
                        planner,
                        worker,
                        turn.core.position,
                        "migration_worker_escort",
                    )
                ):
                    unassigned.pop(worker_id, None)
            return incoming_deposit

        if True:
            # 工人中行星轨道(与游侠共享):发现资源 → 现有经济逻辑(上方已处理采集/回仓);
            # 空闲(无货、无资源目标) → 上中轨绕 Core 转圈巡逻。游侠占内层、工人接外层,
            # 共用 _lightning_assign_shared_middle_lanes 的统一有序队列,点亮外围迷雾。
            # NEAR 勤王 → 工人回 Core 卡位肉盾(游侠躲工人后面狙击)。
            tier = self._lightning_defense_tier(turn)
            if tier == "NEAR":
                for worker_id, worker in list(unassigned.items()):
                    self.memory.clear_worker_goal(worker)
                    if _distance(worker.position, turn.core.position) > 0:
                        planner.toward(
                            worker,
                            turn.core.position,
                            "lightning_worker_meatshield",
                        )
                    unassigned.pop(worker_id, None)
                return incoming_deposit
            for worker_id, worker in list(unassigned.items()):
                self.memory.clear_worker_goal(worker)
                orbit = self._lightning_orbit_waypoint(
                    turn, worker, UnitType.WORKER
                )
                if orbit is not None and not self._lightning_step_toward(
                    turn, planner, worker, orbit, "lightning_worker_orbit"
                ):
                    worker.wait()
                unassigned.pop(worker_id, None)
            return incoming_deposit

        for worker_id, worker in list(unassigned.items()):
            if full_capacity:
                # 满仓：不派新探索目标，工人就地驻守等待 core 腾空间
                continue
            target = self._frontier_target(
                turn,
                worker,
                reserved_targets,
                planner,
                wide_search=wide_resource_search,
            )
            if target is None:
                continue
            goal_kind = (
                "resource_sweep"
                if resource_sweep_active
                else "develop_frontier"
                if develop_wide_search
                else "frontier"
            )
            self.memory.set_worker_goal(worker, goal_kind, target, turn.tick)
            if wide_resource_search:
                self.memory.worker_search_radius[str(worker.id)] = max(
                    self.memory.worker_search_radius.get(str(worker.id), 0),
                    _distance(turn.core.position, target),
                )
            reserved_targets.add(target)
            if self._worker_toward(
                turn,
                planner,
                worker,
                target,
                goal_kind,
            ):
                unassigned.pop(worker_id, None)
                if develop_wide_search:
                    self.memory.decision_totals["worker:develop_explore"] += 1
                elif resource_sweep_active:
                    self.memory.decision_totals["worker:resource_sweep"] += 1
        return incoming_deposit

    def _assign_worker_targets(
        self,
        turn: Turn,
        planner: MovementPlanner,
        unassigned: dict[UUID, Worker],
        targets: set[Position],
        reserved_targets: set[Position],
        *,
        kind: str,
    ) -> None:
        pairs = sorted(
            (
                _distance(worker.position, target),
                self.memory.visited.get(target, 0),
                target,
                worker.id.bytes,
                worker.id,
            )
            for worker in unassigned.values()
            for target in targets
            if target not in reserved_targets
        )
        assigned_workers: set[UUID] = set()
        assigned_targets: set[Position] = set()
        for _, _, target, _, worker_id in pairs:
            if worker_id in assigned_workers or target in assigned_targets:
                continue
            worker = unassigned.get(worker_id)
            if worker is None:
                continue
            self.memory.set_worker_goal(worker, kind, target, turn.tick)
            if self._worker_toward(turn, planner, worker, target, kind):
                assigned_workers.add(worker_id)
                assigned_targets.add(target)
        for worker_id in assigned_workers:
            unassigned.pop(worker_id, None)
        reserved_targets.update(assigned_targets)

    def _assign_recovery_target(
        self,
        turn: Turn,
        planner: MovementPlanner,
        unassigned: dict[UUID, Worker],
        reserved_targets: set[Position],
        decisions: list[str],
    ) -> None:
        if not unassigned:
            return
        assert turn.core is not None
        configured_targets = [
            position
            for position in self.memory.recovery_targets
            if position not in turn.resource_cells and position not in reserved_targets
        ]
        if not configured_targets:
            return

        scout_limit = max(1, min(2, len(turn.workers) // 4))
        active = sorted(
            [
            (worker, goal)
            for worker in turn.workers
            if (goal := self.memory.worker_goals.get(str(worker.id))) is not None
            and goal.kind == "resource_recovery"
            and goal.position in configured_targets
            ],
            key=lambda item: (item[1].created_tick, item[0].id.bytes),
        )
        for worker, goal in active[scout_limit:]:
            self.memory.clear_worker_goal(worker)
        active = active[:scout_limit]
        active_targets = {goal.position for _, goal in active}

        for worker, goal in active:
            if worker.id not in unassigned:
                continue
            target = goal.position
            previous_goal = self.memory.worker_goals.get(str(worker.id))
            self.memory.set_worker_goal(
                worker,
                "resource_recovery",
                target,
                goal.created_tick,
            )
            if self._worker_toward(
                turn,
                planner,
                worker,
                target,
                "resource_recovery:continue",
            ):
                reserved_targets.add(target)
                unassigned.pop(worker.id, None)
                decisions.append(
                    f"resource_recovery_continued worker={_short_id(worker.id)} "
                    f"target={target} distance={_distance(worker.position, target)}"
                )
                self.memory.decision_totals["resource_recovery:continued"] += 1
                continue
            if previous_goal is None:
                self.memory.clear_worker_goal(worker)
            else:
                self.memory.worker_goals[str(worker.id)] = previous_goal

        available_slots = scout_limit - len(active)
        if available_slots <= 0 or not unassigned:
            return
        pending_targets = [
            target
            for target in configured_targets
            if target not in active_targets and target not in reserved_targets
        ]
        pairs = sorted(
            (
                _distance(worker.position, target),
                target_index,
                worker.id.bytes,
                worker.id,
                target,
            )
            for target_index, target in enumerate(pending_targets)
            for worker in unassigned.values()
        )
        assigned_workers: set[UUID] = set()
        assigned_targets: set[Position] = set()
        for _, _, _, worker_id, target in pairs:
            if available_slots <= 0:
                break
            if worker_id in assigned_workers or target in assigned_targets:
                continue
            worker = unassigned.get(worker_id)
            if worker is None:
                continue
            previous_goal = self.memory.worker_goals.get(str(worker.id))
            self.memory.set_worker_goal(
                worker,
                "resource_recovery",
                target,
                turn.tick,
            )
            if self._worker_toward(turn, planner, worker, target, "resource_recovery"):
                assigned_workers.add(worker.id)
                assigned_targets.add(target)
                reserved_targets.add(target)
                available_slots -= 1
                decisions.append(
                    f"resource_recovery_assigned worker={_short_id(worker.id)} "
                    f"target={target} distance={_distance(worker.position, target)}"
                )
                self.memory.decision_totals["resource_recovery:assigned"] += 1
                continue
            if previous_goal is None:
                self.memory.clear_worker_goal(worker)
            else:
                self.memory.worker_goals[str(worker.id)] = previous_goal
        for worker_id in assigned_workers:
            unassigned.pop(worker_id, None)

    def _refill_probe_limit(self, turn: Turn) -> int:
        if False:
            # A seven-Worker economy can afford three concurrent probes of
            # known productive chunks while four Workers retain local coverage.
            return min(3, max(1, (len(turn.workers) + 1) // 2))
        if False:
            # Keep at least half the Workers on local sweep/deposit duty while
            # up to three revisit productive chunks after their refill Tick.
            return min(3, max(1, (len(turn.workers) + 1) // 2))
        return max(1, len(turn.workers) // 3)

    def _refill_probe_core_leash_distance(self, owns_beacon: bool) -> int:
        if owns_beacon:
            return REFILL_PROBE_CORE_LEASH_DISTANCE
        if False:
            return DEVELOP_REFILL_PROBE_CORE_LEASH_DISTANCE
        if False:
            return AGGRESS_REFILL_PROBE_CORE_LEASH_DISTANCE
        if False:
            return BEACON_REFILL_PROBE_CORE_LEASH_DISTANCE
        return REFILL_PROBE_CORE_LEASH_DISTANCE

    def _trim_refilled_chunk_goals(
        self,
        turn: Turn,
        unassigned: dict[UUID, Worker],
        decisions: list[str],
    ) -> None:
        probe_limit = self._refill_probe_limit(turn)
        candidates: list[tuple[int, int, int, int, bytes, UUID, Chunk]] = []
        strategic_trimmed = 0
        owns_beacon = _owns_beacon(turn)
        strategic_beacon = None if owns_beacon else turn.beacon.position
        core_leash_distance = self._refill_probe_core_leash_distance(owns_beacon)
        for worker_id, worker in unassigned.items():
            goal = self.memory.worker_goals.get(str(worker_id))
            if goal is None or goal.kind != "refilled_chunk":
                continue
            outside_core_leash = (
                turn.core is not None
                and _distance(goal.position, turn.core.position)
                > core_leash_distance
            )
            if outside_core_leash or not _refill_probe_allowed(
                worker.position, goal.position, strategic_beacon
            ):
                self.memory.clear_worker_goal(worker)
                strategic_trimmed += 1
                continue
            chunk = _chunk_of(goal.position)
            candidates.append(
                (
                    -self.memory.chunk_harvests.get(chunk, 0),
                    -_chunk_quota(chunk),
                    goal.created_tick,
                    _distance(worker.position, goal.position),
                    worker.id.bytes,
                    worker_id,
                    chunk,
                )
            )

        if strategic_trimmed:
            decisions.append(
                f"refill_probe_strategic_trimmed count={strategic_trimmed}"
            )
            self.memory.decision_totals[
                "refill_probe:strategic_trimmed"
            ] += strategic_trimmed

        kept_workers: set[UUID] = set()
        kept_chunks: set[Chunk] = set()
        for *_, worker_id, chunk in sorted(candidates):
            if len(kept_workers) >= probe_limit or chunk in kept_chunks:
                continue
            kept_workers.add(worker_id)
            kept_chunks.add(chunk)

        trimmed = 0
        for *_, worker_id, _chunk in candidates:
            if worker_id in kept_workers:
                continue
            self.memory.clear_worker_goal(unassigned[worker_id])
            trimmed += 1

        if trimmed:
            decisions.append(
                f"refill_probe_trimmed count={trimmed} active_cap={probe_limit}"
            )
            self.memory.decision_totals["refill_probe:trimmed"] += trimmed

    def _assign_refilled_chunks(
        self,
        turn: Turn,
        planner: MovementPlanner,
        unassigned: dict[UUID, Worker],
        reserved_targets: set[Position],
    ) -> None:
        if not unassigned:
            return
        assert turn.core is not None
        owns_beacon = _owns_beacon(turn)
        strategic_beacon = None if owns_beacon else turn.beacon.position
        strategic_core = (
            turn.core.position
            if owns_beacon or False
            else None
        )
        core_leash_distance = self._refill_probe_core_leash_distance(owns_beacon)
        active_chunks = {
            _chunk_of(goal.position)
            for goal in self.memory.worker_goals.values()
            if goal.kind == "refilled_chunk"
        }
        available_slots = self._refill_probe_limit(turn) - len(active_chunks)
        if available_slots <= 0:
            return
        due_chunks = sorted(
            (
                chunk
                for chunk, refill_tick in self.memory.chunk_next_refill.items()
                if refill_tick <= turn.tick
                and turn.tick - self.memory.chunk_last_probe.get(chunk, -1000)
                >= (
                    AGGRESS_REFILL_PROBE_RECHECK_TICKS
                    if False
                    else 8
                )
                and chunk not in active_chunks
                and _distance(
                    turn.core.position,
                    self.memory.chunk_anchors.get(
                        chunk,
                        (chunk[0] * CHUNK_SIZE + 16, chunk[1] * CHUNK_SIZE + 16),
                    ),
                )
                <= core_leash_distance
            ),
            key=lambda chunk: (
                0
                if owns_beacon
                or _distance(
                    self.memory.chunk_anchors.get(
                        chunk,
                        (chunk[0] * CHUNK_SIZE + 16, chunk[1] * CHUNK_SIZE + 16),
                    ),
                    turn.beacon.position,
                )
                <= _distance(turn.core.position, turn.beacon.position)
                else 1,
                -self.memory.chunk_harvests.get(chunk, 0),
                _distance(
                    turn.core.position,
                    self.memory.chunk_anchors.get(
                        chunk,
                        (chunk[0] * CHUNK_SIZE + 16, chunk[1] * CHUNK_SIZE + 16),
                    ),
                ),
                -_chunk_quota(chunk),
                chunk,
            ),
        )
        for chunk in due_chunks:
            if not unassigned or available_slots <= 0:
                return
            worker = min(
                unassigned.values(),
                key=lambda candidate: (
                    _distance(
                        candidate.position,
                        self.memory.chunk_anchors.get(
                            chunk,
                            (chunk[0] * CHUNK_SIZE + 16, chunk[1] * CHUNK_SIZE + 16),
                        ),
                    ),
                    candidate.id.bytes,
                ),
            )
            target = self._chunk_probe_target(
                chunk,
                turn.tick,
                worker.id,
                planner,
                worker.position,
                strategic_beacon,
                strategic_core,
                core_leash_distance,
            )
            if target is None or target in reserved_targets:
                continue
            self.memory.set_worker_goal(worker, "refilled_chunk", target, turn.tick)
            if self._worker_toward(turn, planner, worker, target, "refilled_chunk"):
                self.memory.chunk_last_probe[chunk] = turn.tick
                reserved_targets.add(target)
                unassigned.pop(worker.id, None)
                active_chunks.add(chunk)
                available_slots -= 1

    def _chunk_probe_target(
        self,
        chunk: Chunk,
        tick: int,
        worker_id: UUID,
        planner: MovementPlanner,
        origin: Position,
        strategic_beacon: Position | None,
        strategic_core: Position | None,
        core_leash_distance: int,
    ) -> Position | None:
        base_x = chunk[0] * CHUNK_SIZE
        base_y = chunk[1] * CHUNK_SIZE
        offsets = ((8, 8), (24, 8), (8, 24), (24, 24), (16, 16))
        rotation = (tick // 4 + worker_id.int) % len(offsets)
        ordered = offsets[rotation:] + offsets[:rotation]
        for dx, dy in ordered:
            position = (base_x + dx, base_y + dy)
            if (
                position not in planner.obstacles
                and _refill_probe_allowed(origin, position, strategic_beacon)
                and (
                    strategic_core is None
                    or _distance(position, strategic_core) <= core_leash_distance
                )
            ):
                return position
        return None

    def _frontier_target(
        self,
        turn: Turn,
        worker: Worker,
        reserved_targets: set[Position],
        planner: MovementPlanner,
        *,
        wide_search: bool = False,
    ) -> Position | None:
        assert turn.core is not None
        label = self.memory.unit_labels.get(str(worker.id))
        worker_number = label.number if label is not None else worker.id.int
        preferred_vectors = (
            (1, 0),
            (1, 1),
            (0, 1),
            (-1, 1),
            (-1, 0),
            (-1, -1),
            (0, -1),
            (1, -1),
        )
        preferred_vector = preferred_vectors[(worker_number - 1) % 8]
        candidates: set[Position] = set()
        if wide_search:
            if False:
                # Resource recovery uses its own bounded local sweep state.
                # Do not reuse the Develop-mode radius, which may have grown
                # to 48 before the mode changed.
                completed_radius = min(
                    self.memory.worker_search_radius.get(str(worker.id), 0),
                    AGGRESS_RESOURCE_SWEEP_MAX_RADIUS,
                )
                current_radius = _distance(turn.core.position, worker.position)
                next_radius = max(
                    AGGRESS_RESOURCE_SWEEP_INITIAL_RADIUS,
                    min(
                        AGGRESS_RESOURCE_SWEEP_MAX_RADIUS,
                        max(
                            completed_radius + AGGRESS_RESOURCE_SWEEP_STEP,
                            current_radius + AGGRESS_RESOURCE_SWEEP_STEP,
                        ),
                    ),
                )
                radii = tuple(
                    radius
                    for radius in (
                        next_radius,
                        next_radius - AGGRESS_RESOURCE_SWEEP_STEP,
                        next_radius - AGGRESS_RESOURCE_SWEEP_STEP * 2,
                    )
                    if AGGRESS_RESOURCE_SWEEP_INITIAL_RADIUS <= radius
                    <= AGGRESS_RESOURCE_SWEEP_MAX_RADIUS
                )
            elif False:
                completed_radius = min(
                    self.memory.worker_search_radius.get(str(worker.id), 0),
                    BEACON_RESOURCE_SWEEP_MAX_RADIUS,
                )
                current_radius = _distance(turn.core.position, worker.position)
                next_radius = max(
                    BEACON_RESOURCE_SWEEP_INITIAL_RADIUS,
                    min(
                        BEACON_RESOURCE_SWEEP_MAX_RADIUS,
                        max(
                            completed_radius + BEACON_RESOURCE_SWEEP_STEP,
                            current_radius + BEACON_RESOURCE_SWEEP_STEP,
                        ),
                    ),
                )
                radii = tuple(
                    radius
                    for radius in (
                        next_radius,
                        next_radius - BEACON_RESOURCE_SWEEP_STEP,
                    )
                    if BEACON_RESOURCE_SWEEP_INITIAL_RADIUS <= radius
                    <= BEACON_RESOURCE_SWEEP_MAX_RADIUS
                )
            else:
                completed_radius = self.memory.worker_search_radius.get(
                    str(worker.id),
                    0,
                )
                current_radius = _distance(turn.core.position, worker.position)
                if completed_radius > 0:
                    next_radius = max(
                        completed_radius + DEVELOP_SEARCH_STEP,
                        current_radius + DEVELOP_SEARCH_STEP,
                    )
                else:
                    next_radius = max(
                        DEVELOP_SEARCH_INITIAL_RADIUS,
                        (
                            current_radius + DEVELOP_SEARCH_STEP
                            if current_radius >= DEVELOP_SEARCH_INITIAL_RADIUS
                            else DEVELOP_SEARCH_INITIAL_RADIUS
                        ),
                    )
                next_radius = min(next_radius, DEVELOP_WIDE_SEARCH_MAX_RADIUS)
                if next_radius >= DEVELOP_WIDE_SEARCH_MAX_RADIUS:
                    # 外环被障碍/已访问格占满时，补扫内层，避免在少数可走格之间来回。
                    radii = tuple(
                        range(
                            next_radius,
                            max(4, next_radius - DEVELOP_SEARCH_STEP) - 1,
                            -4,
                        )
                    )
                else:
                    radii = tuple(
                        radius
                        for radius in (next_radius, next_radius + 8, next_radius + 16)
                        if radius <= DEVELOP_WIDE_SEARCH_MAX_RADIUS
                    )
            if not radii:
                return None
        else:
            radii = (5, 8, 11)
        for radius in radii:
            for dx in range(-radius, radius + 1):
                dy = radius - abs(dx)
                candidates.add((turn.core.position[0] + dx, turn.core.position[1] + dy))
                candidates.add((turn.core.position[0] + dx, turn.core.position[1] - dy))
        candidates.difference_update(planner.obstacles)
        candidates.difference_update(reserved_targets)
        candidates = {
            position
            for position in candidates
            if self.memory.temporary_blocks.get(position, 0) <= turn.tick
        }
        # 目标已抵达时禁止再次选择当前格，否则 goal 会不断刷新但没有移动动作。
        candidates.discard(worker.position)
        if not candidates:
            return None
        owns_beacon = _owns_beacon(turn)
        core_beacon_distance = _distance(turn.core.position, turn.beacon.position)

        def score(position: Position) -> tuple[float, Position]:
            dx = position[0] - turn.core.position[0]
            dy = position[1] - turn.core.position[1]
            distance = max(1, abs(dx) + abs(dy))
            alignment = dx * preferred_vector[0] + dy * preferred_vector[1]
            vector_scale = max(1, abs(preferred_vector[0]) + abs(preferred_vector[1]))
            direction_penalty = (
                distance - alignment / vector_scale
            ) * (5 if wide_search else 2)
            heading_penalty = 0.0
            if not wide_search and self.memory.core_heading is not None:
                heading_x, heading_y = self.memory.core_heading.delta
                forward = dx * heading_x + dy * heading_y
                heading_penalty = max(0.0, 3.0 - forward) * 2.5
            crowd_penalty = sum(max(0, 6 - _distance(position, other)) for other in reserved_targets)
            beacon_progress = 0
            if not wide_search and not owns_beacon:
                beacon_progress = (
                    core_beacon_distance
                    - _distance(position, turn.beacon.position)
                )
            value = (
                self.memory.visited.get(position, 0) * 20
                + planner.threat.get(position, 0) * 20
                + direction_penalty
                + heading_penalty
                + crowd_penalty
                + _distance(worker.position, position) * 0.2
                - _chunk_quota(_chunk_of(position)) * 0.35
                - beacon_progress * BEACON_PROGRESS_WEIGHT
            )
            return value, position

        return min(candidates, key=score)

    def _choose_aggress_heal_rotations(
        self,
        turn: Turn,
        planner: MovementPlanner,
        acted_units: set[UUID],
        decisions: list[str],
    ) -> None:
        rotations = self.memory.aggress_heal_rotations
        live_units = {str(unit.id): unit for unit in turn.units}
        carrier_id = (
            str(turn.beacon.carrier_id)
            if turn.beacon.carrier_id is not None
            else None
        )
        _, beacon_vanguard_guards, beacon_ranger_guards = (
            self._aggress_beacon_guard_assignments(
                turn,
                apply_rotations=False,
            )
        )
        beacon_guard_ids = beacon_vanguard_guards | beacon_ranger_guards
        beacon_guard_keys = {str(unit_id) for unit_id in beacon_guard_ids}

        retained_swaps: list[HealRoleSwap] = []
        for swap in self.memory.aggress_heal_role_swaps:
            patient = live_units.get(swap.patient_id)
            relief = live_units.get(swap.relief_id)
            if (
                patient is not None
                and relief is not None
                and patient.unit_type is relief.unit_type
                and swap.patient_id != carrier_id
                and swap.relief_id != carrier_id
            ):
                retained_swaps.append(swap)
            else:
                decisions.append(
                    "heal_role_swap_retired "
                    f"patient={swap.patient_id[:8]} relief={swap.relief_id[:8]}"
                )
                self.memory.decision_totals["heal_role_swap:retired"] += 1
        self.memory.aggress_heal_role_swaps = retained_swaps

        for patient_id, rotation in tuple(rotations.items()):
            patient = live_units.get(patient_id)
            relief = live_units.get(rotation.relief_id)
            max_hp = MAX_HP.get(patient.unit_type) if patient is not None else None
            if (
                patient is None
                or relief is None
                or patient.unit_type is not relief.unit_type
                or patient_id == carrier_id
                or rotation.relief_id == carrier_id
                or (
                    rotation.phase != "return"
                    and rotation.relief_id in beacon_guard_keys
                )
            ):
                rotations.pop(patient_id, None)
                cancellation_reason = (
                    "beacon_convoy"
                    if patient_id == carrier_id
                    or rotation.relief_id == carrier_id
                    or (
                        rotation.phase != "return"
                        and rotation.relief_id in beacon_guard_keys
                    )
                    else "unit_unavailable"
                )
                decisions.append(
                    "heal_rotation_cancelled "
                    f"patient={patient_id[:8]} reason={cancellation_reason}"
                )
                self.memory.decision_totals["heal_rotation:cancelled"] += 1
            elif max_hp is not None and patient.hp >= max_hp:
                rotations.pop(patient_id, None)
                self.memory.aggress_heal_role_swaps.append(
                    HealRoleSwap(
                        patient_id=patient_id,
                        relief_id=rotation.relief_id,
                        created_tick=turn.tick,
                    )
                )
                decisions.append(
                    "heal_rotation_completed "
                    f"patient={patient_id[:8]} relief={rotation.relief_id[:8]} "
                    "patient_role=core_guard relief_role=frontline"
                )
                self.memory.decision_totals["heal_rotation:completed"] += 1

        recent_damage = any(
            turn.tick - attacked_tick <= AGGRESS_HEAL_ROTATION_QUIET_TICKS
            for attacked_tick in self.memory.attacked_units.values()
        )
        recent_contact = (
            turn.tick - self.memory.last_enemy_visible_tick
            <= AGGRESS_HEAL_ROTATION_QUIET_TICKS
        )
        reinforcement_active, _ = self._aggress_core_reinforcement_state(turn)
        enemy_core_priority = any(
            sighting.is_core for sighting in self.memory.enemy_sightings.values()
        )
        safe = (
            False
            and turn.core is not None
            and turn.core.view.state is CoreState.NORMAL
            and not turn.visible_enemies
            and not recent_damage
            and not recent_contact
            and not reinforcement_active
            and not enemy_core_priority
        )
        if not safe:
            cancelled_rotations = {
                patient_id: rotation
                for patient_id, rotation in rotations.items()
                if rotation.phase != "return"
            }
            for patient_id in cancelled_rotations:
                rotations.pop(patient_id, None)
            if cancelled_rotations:
                reason = (
                    "enemy_core_priority"
                    if enemy_core_priority
                    else "combat_or_core_risk"
                )
                decisions.append(
                    "heal_rotation_cancelled "
                    f"count={len(cancelled_rotations)} reason={reason}"
                )
                self.memory.decision_totals["heal_rotation:cancelled"] += len(
                    cancelled_rotations
                )
            return

        defender_vanguards, defender_rangers = self._aggress_core_defender_ids(turn)
        defender_ids = defender_vanguards | defender_rangers
        reserved_ids = set(rotations)
        reserved_ids.update(rotation.relief_id for rotation in rotations.values())
        comet_busy_ids = self.memory.comet_member_ids | self.memory.comet_retreating_ids
        available_reliefs = [
            unit
            for unit in (*turn.vanguards, *turn.rangers)
            if unit.id not in beacon_guard_ids
            and str(unit.id) not in reserved_ids
            and str(unit.id) not in comet_busy_ids
            and str(unit.id) != carrier_id
            and unit.id not in acted_units
            and unit.hp >= MAX_HP[unit.unit_type]
        ]
        patients = sorted(
            (
                unit
                for unit in (*turn.vanguards, *turn.rangers)
                if unit.id not in defender_ids
                and str(unit.id) not in reserved_ids
                and unit.id not in acted_units
                and str(unit.id) != carrier_id
                and unit.hp < MAX_HP[unit.unit_type]
            ),
            key=lambda unit: (
                unit.hp / MAX_HP[unit.unit_type],
                unit.hp,
                -_distance(unit.position, turn.core.position),
                unit.id.bytes,
            ),
        )
        while (
            turn.resources >= 1
            and patients
            and len(rotations) < AGGRESS_HEAL_ROTATION_MAX
        ):
            patient = patients.pop(0)
            home_reliefs = [
                unit
                for unit in available_reliefs
                if unit.id in defender_ids
            ]
            same_type_home_reliefs = [
                unit
                for unit in home_reliefs
                if unit.unit_type is patient.unit_type
            ]
            frontline_reliefs = [
                unit
                for unit in available_reliefs
                if unit.id not in defender_ids
                and unit.unit_type is patient.unit_type
            ]
            if patient.id in beacon_guard_ids and frontline_reliefs:
                same_type_reliefs = frontline_reliefs
            else:
                home_floor_reached = (
                    len(home_reliefs)
                    <= AGGRESS_HEAL_ROTATION_MIN_HOME_DEFENDERS
                )
                type_floor_reached = (
                    len(same_type_home_reliefs)
                    <= AGGRESS_HEAL_ROTATION_MIN_DEFENDERS_PER_TYPE
                )
                if home_floor_reached or type_floor_reached:
                    continue
                same_type_reliefs = same_type_home_reliefs
            relief = min(
                same_type_reliefs,
                key=lambda unit: (
                    _distance(unit.position, patient.position),
                    unit.id.bytes,
                ),
            )
            available_reliefs.remove(relief)
            rotations[str(patient.id)] = HealRotation(
                relief_id=str(relief.id),
                rendezvous=patient.position,
                phase="relief",
                created_tick=turn.tick,
            )
            decisions.append(
                "heal_rotation_assigned "
                f"patient={_short_id(patient.id)} relief={_short_id(relief.id)} "
                f"type={patient.unit_type.value} rendezvous={patient.position}"
            )
            self.memory.decision_totals["heal_rotation:assigned"] += 1

        for patient_id, rotation in tuple(rotations.items()):
            if rotation.phase != "relief":
                continue
            patient = live_units.get(patient_id)
            relief = live_units.get(rotation.relief_id)
            if patient is None or relief is None:
                continue
            if (
                _distance(patient.position, relief.position)
                <= AGGRESS_HEAL_ROTATION_HANDOFF_RADIUS
            ):
                rotations[patient_id] = HealRotation(
                    relief_id=rotation.relief_id,
                    rendezvous=rotation.rendezvous,
                    phase="return",
                    created_tick=rotation.created_tick,
                )
                decisions.append(
                    "heal_rotation_handoff "
                    f"patient={patient_id[:8]} relief={rotation.relief_id[:8]}"
                )
                self.memory.decision_totals["heal_rotation:handoff"] += 1
                continue

            patient.wait()
            acted_units.add(patient.id)
            if not planner.toward(relief, rotation.rendezvous, "aggress_heal_relief"):
                relief.wait()
            acted_units.add(relief.id)
            decisions.append(
                "heal_rotation_relief_enroute "
                f"patient={patient_id[:8]} relief={rotation.relief_id[:8]} "
                f"rendezvous={rotation.rendezvous}"
            )
            self.memory.decision_totals["heal_rotation:relief_enroute"] += 1

    def _choose_healing(
        self,
        turn: Turn,
        planner: MovementPlanner,
        acted_units: set[UUID],
        decisions: list[str],
    ) -> None:
        core = turn.core
        if core is None:
            return
        healing_candidates: list[Unit]
        healing_reason = "heal_return"
        if False:
            if core.view.state is not CoreState.NORMAL:
                return
            vanguard_defenders, ranger_defenders = (
                self._aggress_core_defender_ids(turn)
            )
            defender_ids = vanguard_defenders | ranger_defenders
            returning_patient_ids = {
                patient_id
                for patient_id, rotation in self.memory.aggress_heal_rotations.items()
                if rotation.phase == "return"
            }
            healing_candidates = [
                unit
                for unit in (*turn.vanguards, *turn.rangers)
                if (
                    unit.id in defender_ids
                    or str(unit.id) in returning_patient_ids
                )
                and (
                    not turn.visible_enemies
                    or str(unit.id) in returning_patient_ids
                )
            ]
            healing_reason = "aggress_guard_heal_return"
        else:
            healing_candidates = list(turn.units)
        for unit in sorted(healing_candidates, key=_uuid_key):
            if unit.id in acted_units:
                continue
            max_hp = MAX_HP.get(unit.unit_type)
            if max_hp is None or unit.hp >= max_hp:
                continue
            if unit.position == core.position:
                if turn.resources >= 1:
                    unit.heal()
                    acted_units.add(unit.id)
                    decisions.append(
                        f"{unit.unit_type.value.lower()}:{_short_id(unit.id)} heal "
                        f"hp={unit.hp}/{max_hp}"
                    )
                    self.memory.decision_totals["unit:heal"] += 1
                elif str(unit.id) in self.memory.aggress_heal_rotations:
                    unit.wait()
                    acted_units.add(unit.id)
                    decisions.append(
                        f"{unit.unit_type.value.lower()}:{_short_id(unit.id)} "
                        "heal_wait reason=insufficient_resources"
                    )
                else:
                    parking = self._core_logistics_parking_target(
                        turn,
                        planner,
                        unit,
                    )
                    moved = (
                        parking is not None
                        and unit.position != parking
                        and planner.toward(unit, parking, "heal_queue_parking")
                    )
                    if not moved:
                        unit.wait()
                    acted_units.add(unit.id)
                    decisions.append(
                        f"{unit.unit_type.value.lower()}:{_short_id(unit.id)} "
                        f"heal_queue_{'parking' if moved else 'hold'} "
                        "reason=insufficient_resources"
                    )
                    self.memory.decision_totals["unit:heal_queue"] += 1
                continue
            rotation_return = (
                self.memory.aggress_heal_rotations.get(str(unit.id))
            )
            reason = (
                "aggress_rotation_heal_return"
                if rotation_return is not None
                and rotation_return.phase == "return"
                else healing_reason
            )
            if turn.resources < 1 and _distance(unit.position, core.position) <= 2:
                parking = self._core_logistics_parking_target(
                    turn,
                    planner,
                    unit,
                )
                moved = (
                    parking is not None
                    and unit.position != parking
                    and planner.toward(unit, parking, "heal_queue_parking")
                )
                if not moved:
                    unit.wait()
                acted_units.add(unit.id)
                decisions.append(
                    f"{unit.unit_type.value.lower()}:{_short_id(unit.id)} "
                    f"heal_queue_{'parking' if moved else 'hold'} "
                    "reason=insufficient_resources"
                )
                self.memory.decision_totals["unit:heal_queue"] += 1
                continue
            if planner.toward(unit, core.position, reason):
                acted_units.add(unit.id)
                decisions.append(
                    f"{unit.unit_type.value.lower()}:{_short_id(unit.id)} heal_return "
                    f"hp={unit.hp}/{max_hp}"
                )
                self.memory.decision_totals["unit:heal_return"] += 1

    def _aggress_heal_role_pairs(
        self,
        turn: Turn,
    ) -> tuple[tuple[Unit, Unit], ...]:
        units_by_id = {str(unit.id): unit for unit in turn.units}
        pairs: list[tuple[Unit, Unit]] = []
        for swap in self.memory.aggress_heal_role_swaps:
            patient = units_by_id.get(swap.patient_id)
            relief = units_by_id.get(swap.relief_id)
            if patient is not None and relief is not None:
                pairs.append((patient, relief))
        for patient_id, rotation in self.memory.aggress_heal_rotations.items():
            if rotation.phase != "return":
                continue
            patient = units_by_id.get(patient_id)
            relief = units_by_id.get(rotation.relief_id)
            if patient is not None and relief is not None:
                pairs.append((patient, relief))
        return tuple(pairs)

    def _aggress_core_defender_ids(
        self,
        turn: Turn,
    ) -> tuple[set[UUID], set[UUID]]:
        carrier, beacon_vanguard_guards, beacon_ranger_guards = (
            self._aggress_beacon_guard_assignments(turn)
        )
        role_pairs = self._aggress_heal_role_pairs(turn)

        def assigned_defenders(
            units: tuple[Unit, ...],
            excluded_ids: set[UUID],
            configured_attackers: int,
            default_defenders: int,
            home_reserve: int,
            minimum_attackers: int,
            unit_type: UnitType,
        ) -> set[UUID]:
            pool = [
                unit
                for unit in sorted(units, key=_uuid_key)
                if unit.id not in excluded_ids
            ]
            if configured_attackers > 0:
                count = max(
                    0,
                    len(pool) - configured_attackers - home_reserve,
                )
            else:
                count = min(
                    default_defenders,
                    len(pool) if carrier is not None else max(0, len(pool) - 1),
                )
                if (
                    home_reserve > 0
                    or len(pool) >= default_defenders + minimum_attackers
                ):
                    count = min(
                        count,
                        max(0, len(pool) - home_reserve - minimum_attackers),
                    )
            preferred_ids = {
                patient.id
                for patient, _ in role_pairs
                if patient.unit_type is unit_type
            }
            relief_ids = {
                relief.id
                for _, relief in role_pairs
                if relief.unit_type is unit_type
            }

            def defender_priority(unit: Unit) -> tuple[int, bytes]:
                if unit.id in preferred_ids:
                    return 0, unit.id.bytes
                if unit.id in relief_ids:
                    return 2, unit.id.bytes
                return 1, unit.id.bytes

            # A completed heal rotation changes who owns a fixed defender slot;
            # it must never increase the configured number of home defenders.
            ordered = sorted(pool, key=defender_priority)
            return {unit.id for unit in ordered[:count]}

        vanguard_excluded = set(beacon_vanguard_guards)
        if carrier is not None:
            vanguard_excluded.add(carrier.id)
        vanguard_defenders = assigned_defenders(
            turn.vanguards,
            vanguard_excluded,
            self.memory.aggress_vanguards,
            1,  # 原 AGGRESS_DEFENDER_VANGUARDS
            0,  # raid 远征已下线，无额外 home 预留
            AGGRESS_MIN_ASSAULT_VANGUARDS,
            UnitType.VANGUARD,
        )
        ranger_defenders = assigned_defenders(
            turn.rangers,
            set(beacon_ranger_guards),
            self.memory.aggress_rangers,
            1,  # 原 AGGRESS_DEFENDER_RANGERS
            0,  # raid 远征已下线，无额外 home 预留
            AGGRESS_MIN_ASSAULT_RANGERS,
            UnitType.RANGER,
        )
        return vanguard_defenders, ranger_defenders

    def _aggress_home_reserve_ids(
        self,
        turn: Turn,
    ) -> tuple[set[UUID], set[UUID]]:
        """Return the non-negotiable 3+3 Core reserve for aggression.

        The operation-bar attacker counts may reduce a larger garrison, but a
        Core coordinate must never cause the last six defenders to be borrowed.
        This is deliberately separate from the current patrol assignment: the
        reserve remains a hard exclusion for assaults and beacon pursuit.
        """
        if (
            turn.core is None
            or len(turn.vanguards) < HOME_RESERVE_VANGUARDS
            or len(turn.rangers) < HOME_RESERVE_RANGERS
        ):
            return set(), set()
        return self._minimum_home_reserve_ids(turn)

    def _aggress_action_reserve_ids(
        self,
        turn: Turn,
        *,
        carrier: Vanguard | None = None,
        beacon_vanguard_guards: Iterable[UUID] = (),
        beacon_ranger_guards: Iterable[UUID] = (),
    ) -> tuple[set[UUID], set[UUID]]:
        """Choose the 3+3 Core reserve without stealing an active convoy."""
        if (
            turn.core is None
            or len(turn.vanguards) < HOME_RESERVE_VANGUARDS
            or len(turn.rangers) < HOME_RESERVE_RANGERS
        ):
            return set(), set()
        excluded_vanguards = set(beacon_vanguard_guards)
        if carrier is not None:
            excluded_vanguards.add(carrier.id)
        return self._minimum_home_reserve_ids(
            turn,
            excluded_vanguards=excluded_vanguards,
            excluded_rangers=set(beacon_ranger_guards),
        )

    def _core_assault_assignments(
        self,
        turn: Turn,
        core_target: Position | None,
    ) -> tuple[bool, set[UUID], set[UUID], Position | None]:
        """Stage a separate force before breaching a known defended Core.

        The check is deliberately limited to a nearby, still-actionable Core.
        It keeps the fixed 3+3 home reserve untouched, calls every surplus
        combat unit to a shared rally cell, and releases the attack only once
        the 1 Vanguard + 2 Ranger breach minimum is actually together.
        """
        if (
            True
            or turn.core is None
            or core_target is None
            # A known Core must never redefine the last surviving defenders as
            # "surplus".  Assault staging starts only after the fixed 3+3
            # home garrison is actually rebuilt.
            or len(turn.vanguards) < HOME_RESERVE_VANGUARDS
            or len(turn.rangers) < HOME_RESERVE_RANGERS
            or _distance(turn.core.position, core_target)
            > CORE_ASSAULT_MAX_HOME_DISTANCE
            or self._core_emergency_threats(turn)
            or self._core_recently_damaged(turn)
        ):
            return False, set(), set(), None

        carrier, beacon_vanguard_guards, beacon_ranger_guards = (
            self._aggress_beacon_guard_assignments(turn)
        )
        home_vanguards, home_rangers = self._aggress_action_reserve_ids(
            turn,
            carrier=carrier,
            beacon_vanguard_guards=beacon_vanguard_guards,
            beacon_ranger_guards=beacon_ranger_guards,
        )
        vanguards = [
            unit for unit in turn.vanguards if unit.id not in home_vanguards
        ]
        rangers = [
            unit for unit in turn.rangers if unit.id not in home_rangers
        ]
        if (
            len(vanguards) < CORE_ASSAULT_MIN_VANGUARDS
            or len(rangers) < CORE_ASSAULT_MIN_RANGERS
        ):
            return False, set(), set(), None

        midpoint = (
            (turn.core.position[0] + core_target[0]) // 2,
            (turn.core.position[1] + core_target[1]) // 2,
        )
        rally_candidates = [
            (midpoint[0] + dx, midpoint[1] + dy)
            for dx, dy in CORE_ASSAULT_RALLY_OFFSETS
            if (midpoint[0] + dx, midpoint[1] + dy) not in self.memory.known_obstacles
        ]
        rally = min(
            rally_candidates or [midpoint],
            key=lambda position: (
                _distance(position, core_target),
                _distance(position, turn.core.position),
                position,
            ),
        )
        # A force already clustered near the target is a valid assault group;
        # do not force it to walk back through a midpoint rally.  A partial
        # group still stages only when the rally is meaningfully away from the
        # Core, otherwise the home screen itself would count as "ready".
        nearby_vanguards = sum(
            _distance(unit.position, rally) <= CORE_ASSAULT_RALLY_RANGE
            for unit in vanguards
        )
        nearby_rangers = sum(
            _distance(unit.position, rally) <= CORE_ASSAULT_RALLY_RANGE
            for unit in rangers
        )
        ready = (
            nearby_vanguards >= CORE_ASSAULT_MIN_VANGUARDS
            and nearby_rangers >= CORE_ASSAULT_MIN_RANGERS
        )
        if not ready and (
            _distance(rally, turn.core.position)
            < CORE_ASSAULT_RALLY_MIN_CORE_DISTANCE
        ):
            return False, set(), set(), None
        return (
            ready,
            {unit.id for unit in vanguards},
            {unit.id for unit in rangers},
            rally,
        )

    def _core_assault_ranger_position(
        self,
        ranger: Ranger,
        core_target: Position,
        planner: MovementPlanner,
    ) -> Position | None:
        """Pick a clear range-three straight/diagonal firing cell for a Core."""
        cells = [
            position
            for position in self._firing_cells(core_target, planner.obstacles)
            if _distance(position, core_target) >= 3
            and planner.final_occupancy(position) < 2
        ]
        if not cells:
            return None
        return min(
            cells,
            key=lambda position: (
                planner.threat.get(position, 0),
                _distance(ranger.position, position),
                position,
            ),
        )

    def _core_emergency_threats(self, turn: Turn) -> tuple[UnitView, ...]:
        if turn.core is None:
            return ()
        return tuple(
            enemy
            for enemy in turn.visible_enemies
            if isinstance(enemy, UnitView)
            and enemy.unit_type in {UnitType.VANGUARD, UnitType.RANGER}
            and _distance(enemy.position, turn.core.position)
            <= CORE_EMERGENCY_THREAT_RADIUS
        )

    def _home_guard_shortfall(self, turn: Turn) -> tuple[int, int, int]:
        vanguard_shortfall = max(0, HOME_RESERVE_VANGUARDS - len(turn.vanguards))
        ranger_shortfall = max(0, HOME_RESERVE_RANGERS - len(turn.rangers))
        combat_shortfall = max(
            0,
            HOME_RESERVE_COMBAT - (len(turn.vanguards) + len(turn.rangers)),
        )
        return vanguard_shortfall, ranger_shortfall, combat_shortfall

    def _core_recently_damaged(self, turn: Turn) -> bool:
        return (
            self.memory.last_core_damaged_tick > 0
            and turn.tick - self.memory.last_core_damaged_tick
            <= CORE_DAMAGE_EMERGENCY_TICKS
        )

    def _core_recently_reset(self, turn: Turn) -> bool:
        last_reset_tick = max(
            self.memory.last_core_destroyed_tick,
            self.memory.last_core_respawn_tick,
        )
        return (
            last_reset_tick > 0
            and turn.tick - last_reset_tick <= CORE_RECOVERY_REBUILD_TICKS
        )

    def _home_recovery_active(self, turn: Turn) -> bool:
        if turn.core is None:
            return False
        vanguard_shortfall, ranger_shortfall, combat_shortfall = (
            self._home_guard_shortfall(turn)
        )
        if self._core_emergency_threats(turn):
            return True
        if self._core_recently_damaged(turn):
            return True
        if self._core_recently_reset(turn):
            return (
                vanguard_shortfall > 0
                or ranger_shortfall > 0
                or combat_shortfall > 0
            )
        return (
            vanguard_shortfall > 0
            or ranger_shortfall > 0
            or combat_shortfall > 0
        )

    def _develop_beacon_scout_ids(
        self,
        turn: Turn,
    ) -> tuple[set[UUID], set[UUID]]:
        """Release a stable 1+1 head-start pair without switching workers to Beacon mode."""
        if (
            True
            or _owns_beacon(turn)
            or turn.core is None
            or _distance(turn.core.position, turn.beacon.position)
            < DEVELOP_EARLY_BEACON_MIN_DISTANCE
            or len(turn.vanguards) < DEVELOP_EARLY_BEACON_MIN_VANGUARDS
            or len(turn.rangers) < DEVELOP_EARLY_BEACON_MIN_RANGERS
            or self._core_recently_damaged(turn)
            or self._core_recently_reset(turn)
            or any(
                _distance(turn.core.position, enemy.position)
                <= CORE_EMERGENCY_THREAT_RADIUS
                for enemy in turn.visible_enemies
            )
        ):
            return set(), set()

        vanguard_candidates = [
            unit
            for unit in turn.vanguards
            if unit.hp >= MAX_HP[UnitType.VANGUARD]
        ]
        ranger_candidates = [
            unit
            for unit in turn.rangers
            if unit.hp >= MAX_HP[UnitType.RANGER]
        ]
        if not vanguard_candidates or not ranger_candidates:
            return set(), set()

        # Once a scout starts moving away it remains the farthest candidate, so
        # the assignment stays stable without adding persistent identity state.
        vanguard = max(
            vanguard_candidates,
            key=lambda unit: (
                _distance(unit.position, turn.core.position),
                unit.id.bytes,
            ),
        )
        ranger = max(
            ranger_candidates,
            key=lambda unit: (
                _distance(unit.position, turn.core.position),
                unit.id.bytes,
            ),
        )
        return {vanguard.id}, {ranger.id}

    def _minimum_home_reserve_ids(
        self,
        turn: Turn,
        *,
        excluded_vanguards: Iterable[UUID] = (),
        excluded_rangers: Iterable[UUID] = (),
    ) -> tuple[set[UUID], set[UUID]]:
        if turn.core is None:
            return set(), set()
        excluded_vanguard_ids = set(excluded_vanguards)
        excluded_ranger_ids = set(excluded_rangers)
        vanguard_pool = sorted(
            (unit for unit in turn.vanguards if unit.id not in excluded_vanguard_ids),
            key=lambda unit: (_distance(unit.position, turn.core.position), unit.id.bytes),
        )
        ranger_pool = sorted(
            (unit for unit in turn.rangers if unit.id not in excluded_ranger_ids),
            key=lambda unit: (_distance(unit.position, turn.core.position), unit.id.bytes),
        )
        reserved_vanguards = {
            unit.id for unit in vanguard_pool[:HOME_RESERVE_VANGUARDS]
        }
        reserved_rangers = {
            unit.id for unit in ranger_pool[:HOME_RESERVE_RANGERS]
        }
        return reserved_vanguards, reserved_rangers

    def _beacon_home_reserve_ids(
        self,
        turn: Turn,
    ) -> tuple[set[UUID], set[UUID]]:
        """Keep the promised 3+3 Core reserve out of beacon actions.

        Explicit beacon control remains usable during an early, incomplete
        opening; once the complete home reserve exists it is never borrowed by
        the expedition.
        """
        home_vanguards, home_rangers = self._minimum_home_reserve_ids(turn)
        reserve_complete = (
            len(home_vanguards) >= HOME_RESERVE_VANGUARDS
            and len(home_rangers) >= HOME_RESERVE_RANGERS
        )
        if self._home_recovery_active(turn) and not reserve_complete:
            return set(), set()
        return home_vanguards, home_rangers

    def _visible_core_combat_strength(
        self,
        turn: Turn,
        target: Position,
    ) -> int | None:
        visible_core = next(
            (
                enemy
                for enemy in turn.visible_enemies
                if isinstance(enemy, CoreView) and enemy.position == target
            ),
            None,
        )
        if visible_core is None:
            return None
        return sum(
            isinstance(enemy, UnitView)
            and enemy.unit_type in {UnitType.VANGUARD, UnitType.RANGER}
            and _distance(enemy.position, target)
            <= BEACON_EXPEDITION_CORE_GUARD_RADIUS
            for enemy in turn.visible_enemies
        )

    @staticmethod
    def _expedition_center(units: Iterable[Unit]) -> Position:
        positions = [unit.position for unit in units]
        return (
            (min(position[0] for position in positions)
             + max(position[0] for position in positions))
            // 2,
            (min(position[1] for position in positions)
             + max(position[1] for position in positions))
            // 2,
        )

    @staticmethod
    def _expedition_anchor_step(
        origin: Position,
        target: Position,
        planner: MovementPlanner,
    ) -> Position:
        candidates = [
            _destination(origin, direction)
            for direction in DIRECTION_ORDER
            if _destination(origin, direction) not in planner.obstacles
            and _destination(origin, direction) not in planner.enemy_cells
        ]
        if not candidates:
            return origin
        return min(
            candidates,
            key=lambda position: (
                _distance(position, target),
                planner.threat.get(position, 0),
                position,
            ),
        )

    def _expedition_advance_anchor(
        self,
        origin: Position,
        target: Position,
        planner: MovementPlanner,
    ) -> Position:
        # A one-cell anchor shift is swallowed by the Ranger formation's
        # two-cell radius: every unit can remain in its old slot forever.
        # Follow a real route for the shared stride. A greedy one-cell step
        # bounces backward/forward when the direct cell is blocked because a
        # valid detour initially increases Manhattan distance.
        path = _find_path(
            origin,
            target,
            blocked=set(planner.obstacles) | set(planner.enemy_cells),
            threat=planner.threat,
            visited=Counter(),
        )
        if not path:
            return self._expedition_anchor_step(origin, target, planner)
        anchor = origin
        for direction in path[:BEACON_EXPEDITION_ADVANCE_STRIDE]:
            anchor = _destination(anchor, direction)
        return anchor

    def _beacon_core_focus_anchor(
        self,
        turn: Turn,
        planner: MovementPlanner,
        core_target: Position,
        expedition: Iterable[Unit],
    ) -> Position:
        """Choose a shared low-threat anchor from which the Core is exposed."""
        firing_cells = [
            position
            for position in self._firing_cells(core_target, planner.obstacles)
            if position not in planner.enemy_cells
            and position not in turn.resource_cells
            and planner.final_occupancy(position) < 2
        ]
        if not firing_cells:
            center = self._expedition_center(expedition)
            return self._expedition_anchor_step(center, core_target, planner)
        center = self._expedition_center(expedition)
        return min(
            firing_cells,
            key=lambda position: (
                planner.threat.get(position, 0),
                _distance(position, center),
                _distance(position, core_target),
                position,
            ),
        )

    def _beacon_expedition_order(
        self,
        turn: Turn,
        planner: MovementPlanner,
        home_vanguards: set[UUID],
        home_rangers: set[UUID],
        strategic_target: Position,
        *,
        core_target: Position | None,
        excluded_ids: Iterable[UUID] = (),
    ) -> BeaconExpeditionOrder:
        unavailable_ids = set(excluded_ids)
        expedition = [
            unit
            for unit in (*turn.vanguards, *turn.rangers)
            if (
                unit.id not in home_vanguards
                if isinstance(unit, Vanguard)
                else unit.id not in home_rangers
            )
            and unit.id not in unavailable_ids
        ]
        if not expedition:
            return BeaconExpeditionOrder(
                strategic_target,
                turn.core.position if turn.core is not None else strategic_target,
                "hold",
            )

        center = self._expedition_center(expedition)
        spread = max(_distance(unit.position, center) for unit in expedition)
        core_enemy_strength = (
            self._visible_core_combat_strength(turn, core_target)
            if core_target is not None
            else None
        )
        local_enemy_strength = sum(
            isinstance(enemy, UnitView)
            and enemy.unit_type in {UnitType.VANGUARD, UnitType.RANGER}
            and any(
                _distance(enemy.position, unit.position)
                <= BEACON_EXPEDITION_LOCAL_THREAT_RADIUS
                for unit in expedition
            )
            for enemy in turn.visible_enemies
        )
        enemy_strength = max(
            (
                strength
                for strength in (core_enemy_strength, local_enemy_strength or None)
                if strength is not None
            ),
            default=None,
        )

        active_vanguards = sum(isinstance(unit, Vanguard) for unit in expedition)
        active_rangers = sum(isinstance(unit, Ranger) for unit in expedition)
        if (
            not self._home_recovery_active(turn)
            and (
                active_vanguards < BEACON_EXPEDITION_MIN_ACTIVE_VANGUARDS
                or active_rangers < BEACON_EXPEDITION_MIN_ACTIVE_RANGERS
            )
        ):
            retreating = local_enemy_strength > 0 and turn.core is not None
            return BeaconExpeditionOrder(
                strategic_target,
                (
                    self._expedition_anchor_step(
                        center,
                        turn.core.position,
                        planner,
                    )
                    if retreating
                    else center
                ),
                "retreat" if retreating else "hold_reinforcements",
                enemy_combat_units=enemy_strength,
            )

        if (
            core_target is not None
            and core_enemy_strength is not None
            and core_enemy_strength <= BEACON_EXPEDITION_WEAK_GUARD_MAX
            and local_enemy_strength <= BEACON_EXPEDITION_WEAK_GUARD_MAX
        ):
            nearby = [
                unit
                for unit in expedition
                if _distance(unit.position, core_target)
                <= BEACON_EXPEDITION_OPPORTUNISTIC_RADIUS
            ]
            nearby_vanguards = sum(isinstance(unit, Vanguard) for unit in nearby)
            nearby_rangers = sum(isinstance(unit, Ranger) for unit in nearby)
            if (
                nearby_vanguards >= CORE_ASSAULT_MIN_VANGUARDS
                and nearby_rangers >= CORE_ASSAULT_MIN_RANGERS
            ):
                local_center = self._expedition_center(nearby)
                local_spread = max(
                    _distance(unit.position, local_center) for unit in nearby
                )
                if local_spread <= BEACON_EXPEDITION_COHESION_RADIUS:
                    return BeaconExpeditionOrder(
                        strategic_target,
                        local_center,
                        "weak_core_strike",
                        frozenset(unit.id for unit in nearby),
                        enemy_strength,
                    )

        visible_core = next(
            (
                enemy
                for enemy in turn.visible_enemies
                if isinstance(enemy, CoreView) and enemy.position == core_target
            ),
            None,
        )
        if (
            visible_core is not None
            and visible_core.state is CoreState.NORMAL
            and core_enemy_strength is not None
            and BEACON_EXPEDITION_WEAK_GUARD_MAX
            < core_enemy_strength
            <= BEACON_CORE_FOCUS_MAX_ENEMY_STRENGTH
            and local_enemy_strength <= BEACON_CORE_FOCUS_MAX_ENEMY_STRENGTH
            and enemy_strength is not None
            and enemy_strength < max(2, len(expedition) - 1)
        ):
            return BeaconExpeditionOrder(
                strategic_target,
                self._beacon_core_focus_anchor(
                    turn,
                    planner,
                    core_target,
                    expedition,
                ),
                "core_focus",
                enemy_combat_units=enemy_strength,
            )

        if spread > BEACON_EXPEDITION_ADVANCE_RELEASE_RADIUS:
            regroup_anchor = center
            if not enemy_strength:
                # In a quiet chokepoint, regroup around a forward anchor. A
                # center-only anchor lets one detouring rear unit pull the
                # whole formation backward after every advance step.
                regroup_anchor = self._expedition_advance_anchor(
                    center,
                    strategic_target,
                    planner,
                )
            return BeaconExpeditionOrder(
                strategic_target,
                regroup_anchor,
                "regroup",
                enemy_combat_units=enemy_strength,
            )

        outmatched = (
            enemy_strength is not None
            and enemy_strength >= max(2, len(expedition) - 1)
        )
        if outmatched:
            nearest_target_distance = min(
                _distance(unit.position, core_target) for unit in expedition
            )
            if (
                turn.core is not None
                and nearest_target_distance
                <= BEACON_EXPEDITION_CORE_GUARD_RADIUS + 2
            ):
                anchor = self._expedition_anchor_step(
                    center,
                    turn.core.position,
                    planner,
                )
                phase = "retreat"
            else:
                anchor = center
                phase = "hold_reinforcements"
            return BeaconExpeditionOrder(
                strategic_target,
                anchor,
                phase,
                enemy_combat_units=enemy_strength,
            )

        return BeaconExpeditionOrder(
            strategic_target,
            self._expedition_advance_anchor(center, strategic_target, planner),
            "advance",
            frozenset(unit.id for unit in expedition),
            enemy_strength,
        )

    def _move_beacon_expedition_unit(
        self,
        turn: Turn,
        planner: MovementPlanner,
        unit: Vanguard | Ranger,
        order: BeaconExpeditionOrder,
        formation_slots: dict[UUID, Position],
        decisions: list[str],
    ) -> bool:
        formation_target = formation_slots.get(unit.id, order.formation_anchor)
        if order.phase == "retreat" and turn.core is not None:
            formation_target = (
                self._core_logistics_parking_target(turn, planner, unit)
                or formation_target
            )
        if unit.position == formation_target:
            return False

        phase = "reinforce" if order.phase == "weak_core_strike" else order.phase
        if not planner.toward(
            unit,
            formation_target,
            f"beacon_expedition_{phase}",
        ):
            return False

        role = "vanguard" if isinstance(unit, Vanguard) else "ranger"
        decisions.append(
            f"{role}:{_short_id(unit.id)} expedition_{phase} "
            f"slot={formation_target} strategic={order.strategic_target}"
        )
        self.memory.decision_totals[
            f"{role}:beacon_expedition_{phase}"
        ] += 1
        return True

    def _core_auto_mobility_ready(self, turn: Turn) -> bool:
        if turn.core is None:
            return False
        if self._core_emergency_threats(turn):
            return False
        if self._core_recently_damaged(turn):
            return False
        if self._core_recently_reset(turn):
            return False
        return (
            len(turn.vanguards) >= CORE_AUTO_MOBILITY_MIN_VANGUARDS
            and len(turn.rangers) >= CORE_AUTO_MOBILITY_MIN_RANGERS
            and len(turn.vanguards) + len(turn.rangers)
            >= CORE_AUTO_MOBILITY_MIN_COMBAT
        )

    def _lightning_patrol_radius(self) -> int:
        """恒星轨道半径 r。r>0 时 Core 沿半径 r 的方形周界（中心原点、边长 2r）
        四角轮转巡逻；r=0 未设置，Core 不巡逻（由调用方判定）。"""
        return self.memory.core_orbit_radius

    def _apply_pending_orbit_migration(self, turn: Turn) -> None:
        """r 变更后，若用户未设 core_target，则把 core_target 设为新方形周界上
        离 Core 最近的角，触发迁移到新轨道；到达后上层 Core 决策回落到
        _lightning_patrol_waypoint，Core 在新轨道上巡逻（不停驻）。

        若用户同时设了 core_target，则以用户目标为准——r 变更只更新半径值 +
        清旧巡逻点，不覆盖用户 target（避免"先去轨道角再去用户目标"的离谱路径）。
        """
        if not self.memory.core_orbit_migration_pending:
            return
        self.memory.core_orbit_migration_pending = False
        core = turn.core
        if core is None:
            return
        r = self.memory.core_orbit_radius
        # 旧巡逻点属于旧半径周界，清掉让 _lightning_patrol_waypoint 按新 r 重选。
        self.memory.lightning_patrol_waypoint = None
        if r <= 0:
            # r 被改回 0：Core 不巡逻。若用户也没设 target，Core 停原地。
            return
        # 用户已设目标 → r 变更不覆盖用户指令，只更新半径（上面已清 patrol waypoint）。
        if self.memory.core_target is not None:
            return
        # 用户未设目标 → 自动迁移到新轨道最近角；到达后无 target+未 hold →
        # 落入巡逻分支，Core 在新轨道上立即开始恒星运动（不停驻）。
        corners = ((r, r), (r, -r), (-r, -r), (-r, r))
        nearest = min(corners, key=lambda c: _distance(core.position, c))
        self.memory.core_target = nearest
        self.memory.core_target_kind = "orbit"

    def _lightning_corner_obstructed(self, target: Position) -> bool:
        """目标角周围 5x5 已知障碍是否超限（角埋在乱石堆里,不值得硬凑）。

        known_obstacles 只含走过看过的格,首圈未知区域不触发——那时按正常
        巡逻推进,撞了记下来,下一圈就会提前跳角绕行。
        """
        obstacles = self.memory.known_obstacles
        nearby = 0
        for dx in range(-2, 3):
            for dy in range(-2, 3):
                if (target[0] + dx, target[1] + dy) in obstacles:
                    nearby += 1
                    if nearby > LIGHTNING_CORNER_OBSTACLE_LIMIT:
                        return True
        return False

    def _core_enemy_bias(self, turn: Turn) -> str:
        """退避三舍 / 趁胜追击：返回本 tick Core 迁移应采用的敌方偏置。

        仅在敌方可见时生效；敌方视野消失 → "none"（Core 恢复恒星轨道/转移推进）。
        退避与追击同时开时退避优先（保命优先于抢攻）。优先级低于驻扎（驻扎时本方法
        不被调用——choose_actions 已在 core_hold 处 return）。仅作用于 Core 运动状态
        （恒星巡逻 + star/march/fortify 三种转移模式），驻扎时停驻不进迁移。
        """
        if not turn.visible_enemies:
            return "none"
        if self.memory.core_evade_enemies:
            return "evade"
        if self.memory.core_chase_enemies:
            return "chase"
        return "none"

    def _lightning_patrol_waypoint(self, turn: Turn) -> Position:
        """Core 巡逻点：沿半径 r 的方形周界四角轮转，遇敌绕开。

        越界（环内或环外）时最近角即目标——走到环上的路本身就算正常巡逻。
        到达死区后推进到下一角，形成绕环转圈。
        新增：检查目标象限是否有可见敌方战斗单位，有则跳过该角。
        r=0（未设置）时返回 core 当前位置——Core 停在原地，不巡逻。
        """
        core = turn.core
        pr = self._lightning_patrol_radius()
        if pr <= 0:
            # r 未设置：不巡逻，返回原位，Core 决策上层据此走 hold 分支。
            self.memory.lightning_patrol_waypoint = None
            return core.position
        # 半径 pr 的方形周界四角（顺时针）。
        corners = ((pr, pr), (pr, -pr), (-pr, -pr), (-pr, pr))
        waypoint = self.memory.lightning_patrol_waypoint
        phase = self.memory.lightning_patrol_phase % 4

        # The waypoint survives process restarts in TacticMemory.  Reproject it
        # when a live control/default ring changes: otherwise a Core that was
        # already heading to an old outer corner keeps flying the stale radius
        # until it arrives.  Choosing the nearest new corner retains its orbit
        # direction while applying the new geometry on the very next Tick.
        if waypoint is not None and waypoint not in corners:
            phase = min(range(4), key=lambda i: _distance(waypoint, corners[i]))
            waypoint = corners[phase]
            self.memory.lightning_patrol_phase = phase
            self.memory.lightning_patrol_waypoint = waypoint

        def _in_quadrant(pos: Position, corner: Position) -> bool:
            """判断pos是否在corner所在象限（以原点为中心）。"""
            cx, cy = corner
            px, py = pos
            if cx > 0 and cy > 0:  # 第一象限
                return px > 0 and py > 0
            elif cx > 0 and cy < 0:  # 第四象限
                return px > 0 and py < 0
            elif cx < 0 and cy < 0:  # 第三象限
                return px < 0 and py < 0
            else:  # 第二象限
                return px < 0 and py > 0

        if waypoint is None:
            # 首次：选最近的周界角作起点。
            phase = min(
                range(4),
                key=lambda i: _distance(core.position, corners[i]),
            )
            self.memory.lightning_patrol_phase = phase
            waypoint = corners[phase]
            self.memory.lightning_patrol_waypoint = waypoint
            return waypoint

        # 检查当前目标象限是否有敌方战斗单位
        enemies_in_quadrant = any(
            isinstance(enemy, UnitView)
            and enemy.unit_type in {UnitType.VANGUARD, UnitType.RANGER}
            and _in_quadrant(enemy.position, waypoint)
            for enemy in turn.visible_enemies
        )

        if enemies_in_quadrant:
            # 跳过这个角，进入下一角
            phase = (phase + 1) % 4
            self.memory.lightning_patrol_phase = phase
            waypoint = corners[phase]
            self.memory.lightning_patrol_waypoint = waypoint
            return waypoint

        if _distance(core.position, waypoint) <= CORE_BEACON_HYSTERESIS:
            # 到达死区，推进到下一角。
            phase = (phase + 1) % 4
            self.memory.lightning_patrol_phase = phase
            waypoint = corners[phase]
            self.memory.lightning_patrol_waypoint = waypoint
        elif (
            _distance(core.position, waypoint) > CORE_BEACON_HYSTERESIS * 2
            and self._lightning_corner_obstructed(waypoint)
        ):
            # 动态跳角：目标角尚远却已知埋在乱石堆里 → 提前推下一角绕行。
            phase = (phase + 1) % 4
            self.memory.lightning_patrol_phase = phase
            waypoint = corners[phase]
            self.memory.lightning_patrol_waypoint = waypoint
        return waypoint

    def _lightning_defense_tier(self, turn: Turn) -> str:
        """Compatibility label over T0-T4, calculated from current lane geometry."""
        geometry = self._lightning_orbit_geometry(turn)
        threats = self._lightning_analyze_threats(turn, geometry)
        if self._core_recently_damaged(turn) or any(contact.tier == "T4" for contact in threats):
            return "NEAR"
        if any(contact.tier in {"T3", "T2"} for contact in threats):
            return "MID"
        # Compatibility callers still expose NEAR/MID/FAR, but the split is
        # derived from the final lane gap rather than legacy 6/20/40 rings.
        if any(
            contact.tier == "T1"
            and contact.square_radius <= geometry.r_ranger_outer + geometry.gap
            for contact in threats
        ):
            return "MID"
        if any(contact.tier == "T1" for contact in threats):
            return "FAR"
        return "NONE"

    def _lightning_has_local_threat(self, turn: Turn, unit: Unit) -> bool:
        """检测单位周围 LIGHTNING_LOCAL_THREAT_RADIUS 内是否有敌方战斗单位。

        用于轨道巡逻时的局部威胁感知：即使敌方未深入我方 Core（defense_tier=NONE），
        游侠/先锋在远处巡逻时遇到敌方单位也应避战，防止孤军深入被围杀。
        """
        for enemy in turn.visible_enemies:
            if not (
                isinstance(enemy, UnitView)
                and enemy.unit_type in {UnitType.VANGUARD, UnitType.RANGER}
            ):
                continue
            if _distance(enemy.position, unit.position) <= LIGHTNING_LOCAL_THREAT_RADIUS:
                return True
        return False

    def _lightning_find_nearest_threat(
        self,
        turn: Turn,
    ) -> UnitView | None:
        """找距离己方 Core 最近的敌方战斗单位（VANGUARD/RANGER）。"""
        nearest_threat = None
        min_distance = float("inf")
        for enemy in turn.visible_enemies:
            if not isinstance(enemy, UnitView):
                continue
            if enemy.unit_type not in {UnitType.VANGUARD, UnitType.RANGER}:
                continue
            dist = _distance(enemy.position, turn.core.position)
            if dist < min_distance:
                min_distance = dist
                nearest_threat = enemy
        return nearest_threat

    def _lightning_intercept_position(
        self,
        turn: Turn,
        ranger: Unit,
        threat: UnitView,
    ) -> Position:
        """计算拦截位置：在威胁与 Core 之间，保持射程内（1-3）。

        优先选择：(1)射程内 (2)靠近威胁方向 (3)不贴脸（距离>=2）。
        """
        # 简单策略：朝威胁方向移动，保持距离2-3
        dx = threat.position[0] - ranger.position[0]
        dy = threat.position[1] - ranger.position[1]
        dist = _distance(ranger.position, threat.position)

        if dist <= 2:
            # 太近了，后退一步
            retreat_x = ranger.position[0] - (1 if dx > 0 else -1 if dx < 0 else 0)
            retreat_y = ranger.position[1] - (1 if dy > 0 else -1 if dy < 0 else 0)
            return (retreat_x, retreat_y)
        elif dist >= 4:
            # 太远了，靠近一步
            approach_x = ranger.position[0] + (1 if dx > 0 else -1 if dx < 0 else 0)
            approach_y = ranger.position[1] + (1 if dy > 0 else -1 if dy < 0 else 0)
            return (approach_x, approach_y)
        else:
            # 距离合适（2-3），保持当前位置
            return ranger.position

    def _lightning_kiting_position(
        self,
        turn: Turn,
        ranger: Unit,
        threat: UnitView,
    ) -> Position:
        """计算游击位置：保持射程（2-3），不让敌人贴脸。

        优先：(1)保持距离2-3 (2)避开障碍 (3)不超出追击边界（方环外缘700）。
        """
        dist = _distance(ranger.position, threat.position)
        dx = threat.position[0] - ranger.position[0]
        dy = threat.position[1] - ranger.position[1]

        if dist <= 1:
            # 敌人贴脸了，立即后退
            retreat_x = ranger.position[0] - (1 if dx > 0 else -1 if dx < 0 else 0)
            retreat_y = ranger.position[1] - (1 if dy > 0 else -1 if dy < 0 else 0)
            return (retreat_x, retreat_y)
        elif dist == 2:
            # 距离正好，保持或侧移
            return ranger.position
        elif dist == 3:
            # 距离正好，保持
            return ranger.position
        elif dist >= 4:
            # 敌人撤了，追近一步（但不超过3）
            approach_x = ranger.position[0] + (1 if dx > 0 else -1 if dx < 0 else 0)
            approach_y = ranger.position[1] + (1 if dy > 0 else -1 if dy < 0 else 0)
            # 检查是否超出追击边界（方环外缘700）
            if max(abs(approach_x), abs(approach_y)) > 700:
                # 超界了，停止追击，回轨道
                return ranger.position
            return (approach_x, approach_y)
        else:
            return ranger.position

    def _lightning_vanguard_intercept(
        self,
        turn: Turn,
        vanguard: Unit,
        threat: UnitView,
    ) -> Position:
        """先锋拦截位置：朝威胁方向移动（近战），但不超出近轨道范围。"""
        dx = threat.position[0] - vanguard.position[0]
        dy = threat.position[1] - vanguard.position[1]

        # 朝威胁方向移动一步
        approach_x = vanguard.position[0] + (1 if dx > 0 else -1 if dx < 0 else 0)
        approach_y = vanguard.position[1] + (1 if dy > 0 else -1 if dy < 0 else 0)

        # 检查是否超出近轨道范围（距离 Core 不超过8）
        dist_to_core = _distance((approach_x, approach_y), turn.core.position)
        if dist_to_core > 8:
            # 超出范围，停在原地
            return vanguard.position

        return (approach_x, approach_y)

    def _lightning_calculate_outer_first_orbits(
        self,
        unit_count: int,
        vision_radius: int,
        gap: int,
        inner_radius: int,
        min_units_per_orbit: int = 3,
        ideal_interval: int = 10,
    ) -> list[tuple[int, int]]:
        """电子排布风格的轨道分配：层容量=2n，循环队列填充。

        返回 [(radius, unit_count), ...] 列表。

        策略：
        1. 层容量：第n层容量 = 2*n（层1→2个，层2→4个，层3→6个...）
        2. 填充顺序：循环队列，轮流填充活跃层，层满时移除
        3. 示例（10个单位）：
           - 活跃层[1,2,3]：单位1→层1, 2→层2, 3→层3, 4→层1(满)
           - 活跃层[2,3]：单位5→层2, 6→层3, 7→层2, 8→层3, 9→层2(满)
           - 活跃层[3]：单位10→层3
           - 结果：层1=2个，层2=4个，层3=4个
        4. 半径映射：层n → radius = inner_radius + (n-1)*gap
        """
        if unit_count == 0:
            return []

        # 计算需要的最大层数（求和公式：1+2+...+n = n(n+1))
        max_layers = 1
        while max_layers * (max_layers + 1) < unit_count:
            max_layers += 1
        max_layers = min(max_layers + 1, 20)  # 最多20层

        # 生成层容量列表
        layers = []
        for n in range(1, max_layers + 1):
            radius = inner_radius + (n - 1) * gap
            capacity = 2 * n
            layers.append({'layer': n, 'radius': radius, 'capacity': capacity, 'count': 0})

        # 循环队列填充
        active_layers = list(range(min(3, len(layers))))  # 初始活跃层：前3层
        remaining = unit_count
        unit_idx = 0

        while remaining > 0 and active_layers:
            # 轮流填充活跃层
            for i in list(active_layers):
                if remaining <= 0:
                    break
                layer = layers[i]
                layer['count'] += 1
                remaining -= 1
                unit_idx += 1

                # 层满时移除
                if layer['count'] >= layer['capacity']:
                    active_layers.remove(i)
                    # 如果需要新层且还有未开放的层
                    next_layer_idx = max(active_layers) + 1 if active_layers else len([l for l in layers if l['count'] > 0])
                    if next_layer_idx < len(layers) and remaining > 0:
                        active_layers.append(next_layer_idx)

        # 转换为 [(radius, count), ...] 格式
        distribution = [(l['radius'], l['count']) for l in layers if l['count'] > 0]
        return distribution

    def _lightning_assign_shared_middle_lanes(
        self,
        turn: Turn,
    ) -> dict[str, tuple[int, int]]:
        """游侠+工人共用中行星轨道的单一有序队列分配。

        游侠优先按电子排布填内层(序号 0..rk-1),第一个工人排在最后一个游侠后面用
        同一公式接排(序号 rk..total-1)。新游侠出生→游侠段扩 1→挤出最靠内的工人
        →该工人落到队尾(total-1)。总数不变时不重算,保证位置稳定不抖动。

        返回合并后的 lanes {uid:(radius,group_idx)}(游侠+工人同层,用于 phase_offset
        的 units_at_radius 计算)。结果同时写入 memory.lightning_orbit_lanes[RANGER/WORKER]
        和 memory.lightning_shared_orbit_seq。
        """
        rangers = sorted(turn.rangers, key=_uuid_key)
        workers = sorted(turn.workers, key=_uuid_key)
        rk = len(rangers)
        wk = len(workers)
        total = rk + wk

        ranger_uids = {str(r.id) for r in rangers}
        worker_uids = {str(w.id) for w in workers}
        live_uids = ranger_uids | worker_uids

        seq = self.memory.lightning_shared_orbit_seq
        # 判断是否需要重算:对比 seq 里仍存活的游侠/工人数 vs 当前。
        # 总数不变且 seq 覆盖全部存活单位 → 复用,不抖动。
        cached_rk = sum(1 for uid in seq if uid in ranger_uids)
        cached_wk = sum(1 for uid in seq if uid in worker_uids)
        seq_covers_live = live_uids.issubset(seq.keys()) and not (
            seq.keys() - live_uids
        )
        stable = (cached_rk == rk and cached_wk == wk and seq_covers_live)

        if stable:
            # 复用 cached lanes,只补合并视图。
            merged: dict[str, tuple[int, int]] = {}
            for role_key in (UnitType.RANGER.value, UnitType.WORKER.value):
                merged.update(self.memory.lightning_orbit_lanes.get(role_key, {}))
            return merged

        if total == 0:
            self.memory.lightning_orbit_lanes[UnitType.RANGER.value] = {}
            self.memory.lightning_orbit_lanes[UnitType.WORKER.value] = {}
            self.memory.lightning_shared_orbit_seq = {}
            return {}

        # 统一电子排布:inner=10(先锋层外), gap=5(游侠视野), ideal_interval=10。
        gap = LIGHTNING_ORBIT_LANE_GAP_RADIUS[UnitType.RANGER]
        inner_radius = LIGHTNING_NEAR_ORBIT_RADIUS + gap
        ideal_interval = LIGHTNING_IDEAL_INTERVAL[UnitType.RANGER]
        vision_radius = LIGHTNING_ORBIT_LANE_GAP_RADIUS[UnitType.RANGER]
        distribution = self._lightning_calculate_outer_first_orbits(
            total,
            vision_radius,
            gap,
            inner_radius,
            min_units_per_orbit=LIGHTNING_MIN_UNITS_PER_ORBIT,
            ideal_interval=ideal_interval,
        )
        # 展开成全局位置序列(内→外),每个位置 = (radius, group_idx)。
        positions = [(r, g) for r, cnt in distribution for g in range(cnt)]

        # 游侠占前 rk 个位置(按 uuid 序),工人接后面 wk 个。
        new_seq: dict[str, int] = {}
        for i, r in enumerate(rangers):
            new_seq[str(r.id)] = i
        base = rk
        for j, w in enumerate(workers):
            new_seq[str(w.id)] = base + j

        ranger_lanes: dict[str, tuple[int, int]] = {}
        worker_lanes: dict[str, tuple[int, int]] = {}
        merged = {}
        for uid, idx in new_seq.items():
            pos = positions[idx] if idx < len(positions) else positions[-1]
            merged[uid] = pos
            if uid in ranger_uids:
                ranger_lanes[uid] = pos
            else:
                worker_lanes[uid] = pos

        self.memory.lightning_orbit_lanes[UnitType.RANGER.value] = ranger_lanes
        self.memory.lightning_orbit_lanes[UnitType.WORKER.value] = worker_lanes
        self.memory.lightning_shared_orbit_seq = new_seq
        # 剪枝阵亡单位在点位环里的残留 phase/anchor（防 memory 无限膨胀）。
        for stale in [k for k in self.memory.lightning_orbit_phase if k not in merged]:
            self.memory.lightning_orbit_phase.pop(stale, None)
            self.memory.lightning_orbit_anchor.pop(stale, None)

        logging.info(
            f"[orbit_assign] shared: rk={rk} wk={wk} total={total}, "
            f"distribution={distribution}"
        )
        return merged

    def _lightning_assign_orbit_lanes(
        self,
        turn: Turn,
        role: UnitType,
    ) -> dict[str, tuple[int, int]]:
        """给某 role 的所有存活单位分配 (radius, group_index)。

        VANGUARD:独立近行星轨道(radius=LIGHTNING_NEAR_ORBIT_RADIUS),按 uuid 序在同层
        错开 group_index。
        RANGER/WORKER:由 _lightning_assign_shared_middle_lanes 统一分配(游侠占内层、
        工人接外层,共享同一组同心半径),本方法返回该 role 对应的 lanes。

        同一半径的单位通过 group_index 错开——_lightning_orbit_waypoint 按
        bit-reversal 序把 group_index 映射到点位环上互不相同的角/中点作起点。
        返回 {uid: (radius, group_index)}。缓存到 memory.lightning_orbit_lanes[role.value]。
        """
        if role is UnitType.VANGUARD:
            units = list(turn.vanguards)
            live = {str(u.id) for u in units}
            role_key = UnitType.VANGUARD.value
            stored = self.memory.lightning_orbit_lanes.get(role_key, {})
            for dead in [k for k in stored if k not in live]:
                stored.pop(dead, None)
                self.memory.lightning_orbit_phase.pop(dead, None)
                self.memory.lightning_orbit_anchor.pop(dead, None)

            radius = LIGHTNING_NEAR_ORBIT_RADIUS
            sorted_units = sorted(units, key=lambda u: _uuid_key(u))
            assignments = {
                str(u.id): (radius, idx) for idx, u in enumerate(sorted_units)
            }
            self.memory.lightning_orbit_lanes[role_key] = assignments
            return assignments

        # RANGER/WORKER 共用中轨:统一分配后取该 role 的 lanes。
        self._lightning_assign_shared_middle_lanes(turn)
        role_key = role.value
        return self.memory.lightning_orbit_lanes.get(role_key, {})

    @staticmethod
    def _bit_reverse(value: int, bits: int) -> int:
        """把 value 的低 bits 位逐位反转。bit-reversal 序 = 最远优先(van der Corput)。"""
        result = 0
        for _ in range(bits):
            result = (result << 1) | (value & 1)
            value >>= 1
        return result

    @staticmethod
    def _lightning_ring_waypoints(
        center: Position, radius: int, count: int
    ) -> tuple[Position, ...]:
        """方形周界上 count 个均匀点位，index 0 在右下角，按原 corners 角序绕行。

        点位沿周长(8r)均匀铺设：index k 在弧长 k*(8r/count) 处。count=4 → 四角
        (右下→右上→左上→左下，与原 _lightning_orbit_waypoint 的 corners 一致)；
        count=8 → 四角+四边中点；count=16 → 再逐级细分。index 0 与 count/2 互为对角。
        """
        if count <= 0:
            return ()
        cx, cy = center
        radius = max(1, radius)
        perimeter = 8 * radius
        pts: list[Position] = []
        for k in range(count):
            arc = k * perimeter // count  # 0..8r-1，自右下角 (r,r) 起逆时针绕方环
            if arc < 2 * radius:
                pts.append((cx + radius, cy + radius - arc))
            elif arc < 4 * radius:
                pts.append((cx + radius - (arc - 2 * radius), cy - radius))
            elif arc < 6 * radius:
                pts.append((cx - radius, cy - radius + (arc - 4 * radius)))
            else:
                pts.append((cx - radius + (arc - 6 * radius), cy + radius))
        return tuple(pts)

    def _lightning_orbit_waypoint(
        self,
        turn: Turn,
        unit: Unit,
        role: UnitType,
        lane: int | None = None,
    ) -> Position | None:
        """绕 Core 转的行星轨道下一目标点：点位环 + 反扎堆跳过。

        圆心 = core.position。半径由 _lightning_assign_orbit_lanes 分配。同一半径的
        N 个单位铺 M=max(4, next_pow2(N)) 个均匀点位，每单位按 bit-reversal 序认领一个
        互不相同的角/中点作 anchor（2 单位→对角、4→四角、5+→补边中点），并沿环同向
        逐点位扫过去。到点死区推进、乱石堆提前跳点位、目标点位附近有同环友军→跳过。
        """
        core = turn.core
        if core is None:
            return None

        uid = str(unit.id)

        # 重新分配（顺带剪枝死亡单位）。RANGER/WORKER 共用中轨——必须用合并后的
        # lanes(游侠+工人同层)才能正确算 units_at_radius(phase_offset 错位依据);
        # VANGUARD 独立近行星轨道,用单 role lanes 即可。
        if role is UnitType.VANGUARD:
            lanes = self._lightning_assign_orbit_lanes(turn, role)
        else:
            lanes = self._lightning_assign_shared_middle_lanes(turn)

        if uid not in lanes:
            return None

        radius, group_index = lanes[uid]

        # 点位环：同半径 N 个单位铺 M=max(4, next_pow2(N)) 个均匀点位。
        # (旧实现只有 4 个角，同层 >4 时 phase_offset 同余 -> 必共享角点、永远贴圈。)
        N = max(1, sum(1 for (r, _) in lanes.values() if r == radius))
        M = max(4, 1 << (N - 1).bit_length())
        bits = M.bit_length() - 1
        anchor = self._bit_reverse(group_index, bits) % M
        waypoints = self._lightning_ring_waypoints(core.position, radius, M)

        # offset = 相对 anchor 的推进步数(0..M-1)，持久化。anchor 变化(旧系统迁移、
        # 网格重排/人数变化)时 offset 失去意义 → 重置回 0，让单位回自己 anchor 重新
        # 锚定，避免存量单位起点撞车、同一点位扎堆。
        offset = self.memory.lightning_orbit_phase.get(uid, 0) % M
        if self.memory.lightning_orbit_anchor.get(uid) != anchor:
            offset = 0
            self.memory.lightning_orbit_anchor[uid] = anchor
        target = waypoints[(anchor + offset) % M]

        # 同环友军(反扎堆依据)：同半径的其他单位当前位置 + 当前目标点位序号。
        # VANGUARD 用单 role lanes；RANGER/WORKER 用合并 lanes，两层都从 lanes 按 radius
        # 匹配。**只统计"正在赶路"的友军**（距自己目标点位 > reach，本 tick 不会推进）：
        # 已停驻(到位即走)的友军不占用点位、不挡道——否则满员环(N=M)会死锁：每个单位
        # 都停在各自点位等下一个空位、而空位又被停驻单位占着，谁也无法推进、永远驻停。
        reach = max(2, min(CORE_BEACON_HYSTERESIS, radius // 2))
        en_route: dict[str, tuple[int, Position]] = {}
        for u in turn.units:
            if u.id == unit.id:
                continue
            lane = lanes.get(str(u.id))
            if lane is None or lane[0] != radius:
                continue
            uid2 = str(u.id)
            o_idx = ((self._bit_reverse(lane[1], bits) % M)
                     + (self.memory.lightning_orbit_phase.get(uid2, 0) % M)) % M
            if _distance(u.position, waypoints[o_idx]) > reach:
                en_route[uid2] = (o_idx, u.position)
        claimed = {o_idx for (o_idx, _) in en_route.values()}
        same_ring_blockers = frozenset(pos for (_, pos) in en_route.values())

        # 逐点位推进/跳过：到达死区→推进；乱石堆→提前跳；同环友军占位→跳过；
        # 目标点位已被同环其他单位占用→跳过；访问饱和→提前放弃。循环上限取 M（点位
        # 数）保证只要存在空位就一定能跳过到（同环最多 N-1 个单位占走 N-1 个点位，M≥N
        # 恒有 ≥1 个空位）。点位间距(≈周长/M) 远大于 2*ALLY_RADIUS，追上瞬间干净超车。
        for _ in range(M):
            advanced = False
            if _distance(unit.position, target) <= reach:
                offset = (offset + 1) % M
                advanced = True
            elif (
                _distance(unit.position, target) > CORE_BEACON_HYSTERESIS * 2
                and self._lightning_corner_obstructed(target)
            ):
                offset = (offset + 1) % M
                advanced = True
            elif (
                _distance(unit.position, target) < CORE_BEACON_HYSTERESIS * 2
                and self.memory.visited.get(target, 0) > 10
                and self._lightning_corner_obstructed(target)
            ):
                offset = (offset + 1) % M
                advanced = True
            elif (
                any(
                    _distance(target, fpos) <= LIGHTNING_ORBIT_WAYPOINT_ALLY_RADIUS
                    for fpos in same_ring_blockers
                )
                or (anchor + offset) % M in claimed
            ):
                offset = (offset + 1) % M
                advanced = True
            if not advanced:
                break
            target = waypoints[(anchor + offset) % M]

        self.memory.lightning_orbit_phase[uid] = offset

        # 行星轨道目标 = core 相对角 (cx±radius, cy±radius)，全程跟着 core；
        # 外层离 core 的距离已由 lane 半径控制，无需再施加地图方环约束。

        if target == unit.position:
            return None

        return target

    # ------------------------------------------------------------------
    # 哈雷彗星 (Comet Strike)：派出小队追踪信标或打击自定义坐标。
    # 抽调顺序：轨道外层优先（lightning_orbit_lanes 的 radius 降序）。
    # 替换策略：成员 hp/max_hp < comet_wounded_threshold（默认半血）→ 移入
    #   retreating，回 Core 回血；同一时间从最外层抽同等数量满血替补补员。
    # 自动收兵：信标被己方拾取(beacon 模式成功夺取) / 满血单位不足以保 Core
    #   阈值又无替补时 → comet_active=False，全员回 lightning 巡逻。
    # ------------------------------------------------------------------

    def _comet_resolve_target(self, turn: Turn) -> Position | None:
        """每 tick 刷新彗星目标点，并判定 beacon 模式的成功/跟随状态。

        beacon 模式：目标 = turn.beacon.position（每 tick 动态更新）。信标被己方
        拾取（_owns_beacon）→ 成功夺取，返回 None 表示应收兵。信标在敌方手上或
        仍在地面 → 持续跟随。coordinate 模式：目标 = comet_target（控制文件设定）。
        """
        if not self.memory.comet_active:
            return None
        if self.memory.comet_mode == "coordinate":
            return self.memory.comet_target
        # beacon 模式
        if turn.beacon.status is BeaconStatus.CARRIED and _owns_beacon(turn):
            # 信标已被己方夺取 → 任务成功，收兵。
            self._comet_cancel(turn, reason="beacon_captured")
            return None
        return turn.beacon.position

    def _comet_rally_point(self, turn: Turn, target: Position) -> Position | None:
        """计算集合点：沿 Core→目标 方向、距目标 comet_rally_distance 的格子。

        集合点距目标点 distance 格（用户设定），从目标沿"目标→Core"方向回退
        distance 格——即成员先在该点集合，距目标还剩 distance 路程，等齐队友
        后一起冲最后这段。target 自身也可能不可占（是障碍/敌方占位），但集合点
        只作"近旁占位"的锚点，不需要本身可站——到达判定看 _comet_at_rally。
        distance ≤ 0 或 Core 不可用时返回 target 本身（贴目标点集合）。
        """
        if turn.core is None:
            return target
        distance = self.memory.comet_rally_distance
        if distance <= 0:
            return target
        core_pos = turn.core.position
        dx = target[0] - core_pos[0]
        dy = target[1] - core_pos[1]
        magnitude = (dx * dx + dy * dy) ** 0.5
        if magnitude <= 0:
            return target
        # 从 target 沿目标→Core 方向回退 distance 格：ratio = (mag-distance)/mag。
        ratio = max(0.0, (magnitude - distance) / magnitude)
        return (
            int(round(core_pos[0] + dx * ratio)),
            int(round(core_pos[1] + dy * ratio)),
        )

    def _comet_at_rally(self, turn: Turn, planner: MovementPlanner,
                        unit: Unit, rally: Position) -> bool:
        """判定单位是否已到达集合点近旁（集合点本身可能是障碍物）。

        "到达集合"= 单位所在格距集合点曼哈顿距离 ≤ COMET_RALLY_ARRIVE_RADIUS，
        或单位站在集合点周围近邻任一可占格（集合点是障碍物时绕它一圈占位即可）。
        一格最多待 2 名队员，由 final_occupancy 限制占位格的选取；此处只判定
        "是否已到达近旁"，不负责分配占位格（占位移动交给 _comet_step_safe_toward
        向 rally 推进，成员自然在 rally 周围散开占位）。
        """
        if _distance(unit.position, rally) <= COMET_RALLY_ARRIVE_RADIUS:
            return True
        # 单位还没贴到 rally，但若它已被 _comet_step_safe_toward 推到 rally 周围
        # 近邻可占格（rally 是障碍物时），也算到达。
        for direction in DIRECTION_ORDER:
            neighbor = _destination(unit.position, direction)
            if _distance(neighbor, rally) <= COMET_RALLY_ARRIVE_RADIUS:
                # 邻格在 rally 近旁且可占 → 单位下一步能占过去即视为到达近旁。
                if (
                    neighbor not in planner.obstacles
                    and neighbor not in planner.enemy_cells
                    and planner.final_occupancy(neighbor) < 2
                ):
                    return True
        return False

    def _comet_rally_all_ready(self, turn: Turn) -> bool:
        """首批集合：所有 member（含两兵种）是否都已到达集合点近旁。

        用于决定首批编队是否结束集合、一起向目标推进。retreating 成员不参与
        集合判定（伤员在退血，不影响前线集合推进）。
        """
        if not self.memory.comet_member_ids:
            return False
        live_by_id: dict[str, Unit] = {}
        for role_units in (turn.vanguards, turn.rangers):
            for unit in role_units:
                live_by_id[str(unit.id)] = unit
        return all(
            uid in self.memory.comet_rally_ready_ids
            for uid in self.memory.comet_member_ids
            if uid in live_by_id
        )

    def _comet_cancel(self, turn: Turn, *, reason: str) -> None:
        """取消彗星任务：关闭开关、清空编队，让全员回归 lightning 轨道巡逻。"""
        if not self.memory.comet_active:
            return
        self.memory.comet_active = False
        self.memory.comet_member_ids.clear()
        self.memory.comet_retreating_ids.clear()
        self.memory.comet_target = None
        self.memory.comet_dispatched_tick = 0
        self.memory.comet_rally_ready_ids.clear()
        self.memory.comet_rally_done = False
        self.memory.decision_totals[f"comet:cancel:{reason}"] += 1

    def _comet_pool(self, turn: Turn, role: UnitType) -> list[Unit]:
        """返回某兵种的可用抽调池：非 member、非 retreating、满血。"""
        units = turn.vanguards if role is UnitType.VANGUARD else turn.rangers
        busy = self.memory.comet_member_ids | self.memory.comet_retreating_ids
        max_hp = MAX_HP[role]
        return [
            unit
            for unit in units
            if str(unit.id) not in busy and unit.hp >= max_hp
        ]

    def _comet_orbit_radius(self, uid: str, role: UnitType) -> int:
        """读单位轨道半径；缺失时返回 -1（最内层，抽调优先级最低）。"""
        lanes = self.memory.lightning_orbit_lanes.get(role.value, {})
        entry = lanes.get(uid)
        if entry is None:
            return -1
        # entry 是 (radius, group_index)
        return int(entry[0]) if isinstance(entry, (tuple, list)) and entry else -1

    def _comet_reinforce(self, turn: Turn) -> None:
        """每 tick 维护彗星编队：清伤员、补替补、守保留线、无替补则收兵。

        在 _choose_vanguards/_choose_rangers 之前调用一次。目标规模按兵种分别
        维持 comet_vanguards / comet_rangers。抽调顺序：orbit radius 降序（外层
        优先）。保留线：抽调后轨道满血单位不得低于 comet_min_reserve_*。

        失败取消条件（从严）：前线 0 个 member 在战斗（全员阵亡或全部退下来
        retreating）且基地凑不出任何满血替补（满血池被保留线吃光或为空）。
        只要有任一兵种还在前线打 / 任一兵种还能补员，任务就继续——不因某一
        兵种替补断档而把整个任务连带取消。伤员回血 / Core 新造单位使满血储备
        超过最低守卫阈值后，超出部分继续派往前线（shortfall 补员逻辑不变）。
        """
        if not self.memory.comet_active or turn.core is None:
            return

        # 1) 盘点：清掉阵亡/半血成员 → 移入 retreating。
        # 用一张统一存活表（先锋+游侠合并）判断"阵亡"，避免把先锋成员误当游侠阵亡清掉。
        threshold = self.memory.comet_wounded_threshold
        live_by_id: dict[str, Unit] = {}
        for role_units in (turn.vanguards, turn.rangers):
            for unit in role_units:
                live_by_id[str(unit.id)] = unit
        for uid in list(self.memory.comet_member_ids):
            unit = live_by_id.get(uid)
            if unit is None:
                # 阵亡：直接移除（不进 retreating），并清掉其 rally-ready 标记。
                self.memory.comet_member_ids.discard(uid)
                self.memory.comet_rally_ready_ids.discard(uid)
                continue
            max_hp = MAX_HP[unit.unit_type]
            if unit.hp / max_hp <= threshold:
                self.memory.comet_member_ids.discard(uid)
                self.memory.comet_rally_ready_ids.discard(uid)
                self.memory.comet_retreating_ids.add(uid)

        # 2) 补员：按兵种分别从外层抽调，受保留线约束。
        for role, target_count in (
            (UnitType.VANGUARD, self.memory.comet_vanguards),
            (UnitType.RANGER, self.memory.comet_rangers),
        ):
            role_units = (
                turn.vanguards if role is UnitType.VANGUARD else turn.rangers
            )
            role_uids = {str(u.id) for u in role_units}
            current = sum(
                1
                for uid in self.memory.comet_member_ids
                if uid in role_uids
            )
            shortfall = max(0, target_count - current)
            if shortfall == 0:
                continue
            pool = self._comet_pool(turn, role)
            # 外层优先：radius 降序；并列时 uuid 稳定。
            pool.sort(
                key=lambda u: (
                    -self._comet_orbit_radius(str(u.id), role),
                    u.id.bytes,
                )
            )
            reserve_min = (
                self.memory.comet_min_reserve_vanguards
                if role is UnitType.VANGUARD
                else self.memory.comet_min_reserve_rangers
            )
            # 保留线：抽走 N 个后，轨道满血同兵种 = len(pool) - N 不得低于
            # reserve_min → 最多可抽 max(0, len(pool) - reserve_min) 个。
            dispatchable = max(0, len(pool) - reserve_min)
            for unit in pool[:dispatchable]:
                if shortfall <= 0:
                    break
                self.memory.comet_member_ids.add(str(unit.id))
                shortfall -= 1
            # 注意：此处不再因"某兵种替补补不齐"而过早 cancel 整个任务。
            # 替补断档但其他兵种仍在前线时，任务继续；最终失败判定见下方统一检查。

        # 3) 失败取消判定（从严）：前线 0 个 member 在战斗 + 基地凑不出任何满血替补。
        if self.memory.comet_member_ids and not self.memory.comet_retreating_ids:
            # 还有 member 在前线 → 任务继续，且记录首次出发 tick。
            if self.memory.comet_dispatched_tick == 0:
                self.memory.comet_dispatched_tick = turn.tick
            return
        # 前线空（member 为空）：看 retreating 是否还在退回来（退血路上尚有存活单位）。
        if self.memory.comet_retreating_ids:
            # 伤员还在退血路上 → 视为前线尚有"正在后撤的存活单位"，任务继续，
            # 等他们回满血或新单位造出来再补员。不取消。
            if self.memory.comet_dispatched_tick == 0:
                self.memory.comet_dispatched_tick = turn.tick
            return
        # 前线空、retreating 空：看基地能否凑出任何满血替补（两兵种任一即可）。
        vg_pool = self._comet_pool(turn, UnitType.VANGUARD)
        rk_pool = self._comet_pool(turn, UnitType.RANGER)
        vg_reserve_min = self.memory.comet_min_reserve_vanguards
        rk_reserve_min = self.memory.comet_min_reserve_rangers
        vg_dispatchable = max(0, len(vg_pool) - vg_reserve_min)
        rk_dispatchable = max(0, len(rk_pool) - rk_reserve_min)
        if vg_dispatchable > 0 or rk_dispatchable > 0:
            # 基地还能挤出满血替补（至少 1 个，超过保留线）→ 任务继续。
            if self.memory.comet_dispatched_tick == 0:
                self.memory.comet_dispatched_tick = turn.tick
            return
        # 前线全空 + 退无可退 + 基地凑不出任何满血替补 → 任务失败取消。
        self._comet_cancel(turn, reason="squad_wiped_no_reserve")

    def _comet_advance(
        self,
        turn: Turn,
        planner: MovementPlanner,
        unit: Unit,
        target: Position,
        acted_units: set[UUID],
        decisions: list[str],
    ) -> None:
        """彗星成员向目标推进，但绝不走进敌方战斗单位射程圈内。

        威胁感知交由 _comet_step_safe_toward：下一格距任何敌方战斗单位 ≤
        COMET_THREAT_RADIUS 的候选直接剔除，不再像 _lightning_step_toward 那样
        仅在打分里加权（距目标只差 1 格时距离项会压过威胁项 → 送）。无法安全推进
        时原地 wait，等威胁被清掉或敌方移开再走。途中遇敌由上游 _comet_step_unit
        分流（游侠开火 / 先锋避战），此处只管安全推进。
        """
        if unit.position == target:
            unit.wait()
            acted_units.add(unit.id)
            decisions.append(
                f"{unit.unit_type.value.lower()}:{_short_id(unit.id)} comet_hold "
                f"target={target}"
            )
            self.memory.decision_totals["comet:advance:hold"] += 1
            return
        moved = self._comet_step_safe_toward(
            turn, planner, unit, target, "comet_advance"
        )
        if not moved:
            # 无安全下一格（全部被敌方火力覆盖或被堵死）→ 原地待命，不送。
            unit.wait()
            threats = self._comet_enemy_threats(turn, unit)
            reason = "blocked_by_threat" if threats else "blocked_by_obstacle"
            decisions.append(
                f"{unit.unit_type.value.lower()}:{_short_id(unit.id)} comet_hold "
                f"reason={reason} target={target}"
            )
            self.memory.decision_totals[
                "comet:advance:hold_threat" if threats else "comet:advance:blocked"
            ] += 1
            return
        acted_units.add(unit.id)
        decisions.append(
            f"{unit.unit_type.value.lower()}:{_short_id(unit.id)} comet_advance "
            f"target={target}"
        )
        self.memory.decision_totals["comet:advance:step"] += 1

    def _comet_retreat(
        self,
        turn: Turn,
        planner: MovementPlanner,
        unit: Unit,
        acted_units: set[UUID],
        decisions: list[str],
    ) -> None:
        """彗星伤员回 Core 回血；回满后从 retreating 移除，重入轨道池。"""
        max_hp = MAX_HP[unit.unit_type]
        core = turn.core
        if core is not None and unit.hp >= max_hp and _distance(
            unit.position, core.position
        ) <= 3:
            # 已到家且满血 → 退役 retreating，下 tick 可被重新抽调。
            self.memory.comet_retreating_ids.discard(str(unit.id))
            return
        if core is None:
            return
        # 伤员回血路上同样不得穿过敌方火力射程：用安全推进选下一格。
        moved = self._comet_step_safe_toward(
            turn, planner, unit, core.position, "comet_retreat"
        )
        if not moved:
            unit.wait()
        acted_units.add(unit.id)
        decisions.append(
            f"{unit.unit_type.value.lower()}:{_short_id(unit.id)} comet_retreat "
            f"hp={unit.hp}/{max_hp}"
        )
        self.memory.decision_totals["comet:retreat"] += 1

    def _comet_step_unit(
        self,
        turn: Turn,
        planner: MovementPlanner,
        unit: Unit,
        target: Position | None,
        acted_units: set[UUID],
        decisions: list[str],
        *,
        ledger: ShotLedger | None = None,
        contacts_by_id: dict[UUID, ThreatContact] | None = None,
    ) -> bool:
        """彗星成员的单单位分流。返回 True 表示已处理（调用方跳过 lightning 路径）。

        遇敌分流（修复"碰到敌方攻击单位直接送"的根因）：成员在敌方战斗单位
        COMET_THREAT_RADIUS 内时，不再走 _comet_advance 死磕推进——
          · 游侠：复用 lightning 同源格斗技巧（单杀先锋舞步 + 打分制预瞄）开火清障，
            射程内无合法射击则原地 wait 不前压；
          · 先锋：交给 _comet_advance 的安全推进（下一格进火力圈会被剔除），
            无安全格则原地 wait。
        无威胁时走 _comet_advance 正常推进。
        ledger/contacts_by_id 由游侠调用方（_choose_rangers_lightning）透传，与
        同 tick 其他游侠共享火力配额；未透传时本地建一份，避免彗星游侠过度集火。
        """
        uid = str(unit.id)
        if uid in self.memory.comet_retreating_ids:
            self._comet_retreat(turn, planner, unit, acted_units, decisions)
            return True
        if uid in self.memory.comet_member_ids:
            if target is None:
                # beacon 模式下目标丢失（信标不可见）→ 原地待命，不脱离编队。
                unit.wait()
                acted_units.add(unit.id)
                decisions.append(
                    f"{unit.unit_type.value.lower()}:{_short_id(unit.id)} comet_wait "
                    f"reason=no_target"
                )
                return True
            # 集合（Rally）阶段：仅首批出发编队触发集合。
            # rally_enabled=True 且首轮集合尚未结束（comet_rally_done=False）时，
            # member 先奔集合点近旁占位警戒，等齐队友再一起向目标推进。
            # 替补成员在首轮集合结束后（comet_rally_done=True）才入编 → 直接奔目标。
            if (
                self.memory.comet_rally_enabled
                and not self.memory.comet_rally_done
                and turn.core is not None
            ):
                rally_point = self._comet_rally_point(turn, target)
                if rally_point is not None and not self._comet_at_rally(
                    turn, planner, unit, rally_point
                ):
                    # 还没到集合点近旁 → 先推进到集合点附近，途中遇敌照样还击。
                    self._comet_advance_to_rally(
                        turn, planner, unit, rally_point,
                        threats=self._comet_enemy_threats(turn, unit),
                        acted_units=acted_units, decisions=decisions,
                        ledger=ledger, contacts_by_id=contacts_by_id,
                    )
                    return True
                # 已到集合点近旁 → 标记 ready，原地占位警戒，等齐队友。
                self.memory.comet_rally_ready_ids.add(uid)
                if not self._comet_rally_all_ready(turn):
                    # 还有队友没到 → 原地占位警戒，攻击系统不丢（有敌就打）。
                    if self._comet_rally_hold(
                        turn, planner, unit, rally_point,
                        threats=self._comet_enemy_threats(turn, unit),
                        acted_units=acted_units, decisions=decisions,
                        ledger=ledger, contacts_by_id=contacts_by_id,
                    ):
                        return True
                    # 没威胁可打 → 原地 wait 占位。
                    unit.wait()
                    acted_units.add(unit.id)
                    decisions.append(
                        f"{unit.unit_type.value.lower()}:{_short_id(unit.id)} comet_rally_hold "
                        f"rally={rally_point} waiting_for_squad"
                    )
                    self.memory.decision_totals["comet:rally:hold"] += 1
                    return True
                # 全员到齐 → 结束集合阶段，清空 rally_ready_ids，后续（含本 tick）
                # 一起向目标推进。
                self.memory.comet_rally_ready_ids.clear()
                self.memory.comet_rally_done = True
                self.memory.decision_totals["comet:rally:all_ready"] += 1
            threats = self._comet_enemy_threats(turn, unit)
            if threats and isinstance(unit, Ranger):
                # 游侠远程还击：走与 lightning 同源的格斗技巧（舞步/预瞄）。
                local_ledger = ledger if ledger is not None else ShotLedger()
                local_contacts = contacts_by_id or {}
                if self._comet_ranger_engage(
                    turn, planner, unit, threats,
                    ledger=local_ledger, contacts_by_id=local_contacts,
                    acted_units=acted_units, decisions=decisions,
                ):
                    return True
                # 射程内打不到敌但近旁有威胁 → 原地待命不前压，等下一 tick 敌方进入射程或被己方清掉。
                unit.wait()
                acted_units.add(unit.id)
                decisions.append(
                    f"ranger:{_short_id(unit.id)} comet_hold "
                    f"reason=threat_out_of_range n={len(threats)}"
                )
                self.memory.decision_totals["comet:ranger:hold_threat"] += 1
                return True
            # 先锋 / 游侠无法射击：交给 _comet_advance 走安全推进，进火力圈的格会被剔除。
            self._comet_advance(turn, planner, unit, target, acted_units, decisions)
            return True
        return False

    def _comet_advance_to_rally(
        self,
        turn: Turn,
        planner: MovementPlanner,
        unit: Unit,
        rally: Position,
        *,
        threats: list[UnitView],
        acted_units: set[UUID],
        decisions: list[str],
        ledger: ShotLedger | None = None,
        contacts_by_id: dict[UUID, ThreatContact] | None = None,
    ) -> None:
        """集合阶段：成员向集合点近旁推进，途中遇敌照样还击（攻击系统不丢）。

        游侠遇威胁先开火清障（复用 _comet_ranger_engage），射不到时再安全推进；
        先锋无射击 → 直接安全推进。安全推进复用 _comet_step_safe_toward，下一格
        进敌方火力圈的格会被剔除，集合点本身是障碍物时不影响——成员自然在 rally
        周围近邻可占格散开占位。
        """
        if threats and isinstance(unit, Ranger):
            local_ledger = ledger if ledger is not None else ShotLedger()
            local_contacts = contacts_by_id or {}
            if self._comet_ranger_engage(
                turn, planner, unit, threats,
                ledger=local_ledger, contacts_by_id=local_contacts,
                acted_units=acted_units, decisions=decisions,
            ):
                return
            # 射程内打不到 → 不前压进火力圈，原地 wait。
            unit.wait()
            acted_units.add(unit.id)
            decisions.append(
                f"ranger:{_short_id(unit.id)} comet_rally_advance "
                f"reason=threat_out_of_range rally={rally}"
            )
            self.memory.decision_totals["comet:rally:advance:hold_threat"] += 1
            return
        # 先锋 / 游侠无射击：安全推进向 rally，进火力圈的格会被剔除。
        moved = self._comet_step_safe_toward(
            turn, planner, unit, rally, "comet_rally_advance"
        )
        if not moved:
            unit.wait()
            self.memory.decision_totals["comet:rally:advance:blocked"] += 1
        acted_units.add(unit.id)
        decisions.append(
            f"{unit.unit_type.value.lower()}:{_short_id(unit.id)} comet_rally_advance "
            f"rally={rally}"
        )
        self.memory.decision_totals["comet:rally:advance:step"] += 1

    def _comet_rally_hold(
        self,
        turn: Turn,
        planner: MovementPlanner,
        unit: Unit,
        rally: Position,
        *,
        threats: list[UnitView],
        acted_units: set[UUID],
        decisions: list[str],
        ledger: ShotLedger | None = None,
        contacts_by_id: dict[UUID, ThreatContact] | None = None,
    ) -> bool:
        """集合点原地占位警戒：有威胁就打（攻击系统不丢），无威胁原地 wait。

        返回 True 表示已交战（调用方不再 wait）；False 表示无威胁可打，由调用方
        落到原地 wait。游侠遇敌复用 _comet_ranger_engage；先锋遇近邻敌由上游
        _comet_step_safe_toward 不前压（已到集合点近旁，不再推进）。
        """
        if threats and isinstance(unit, Ranger):
            local_ledger = ledger if ledger is not None else ShotLedger()
            local_contacts = contacts_by_id or {}
            if self._comet_ranger_engage(
                turn, planner, unit, threats,
                ledger=local_ledger, contacts_by_id=local_contacts,
                acted_units=acted_units, decisions=decisions,
            ):
                return True
        return False

    def _comet_enemy_threats(self, turn: Turn, unit: Unit) -> list[UnitView]:
        """返回距该彗星成员 COMET_THREAT_RADIUS 内的敌方战斗单位。

        用敌方游侠射程 3 + 先锋近战 1 取上界作统一警戒圈：进入此圈即视为已处在
        敌方火力射程内，不得继续朝目标直冲，否则就是用户观测到的"直接送"。
        """
        threats: list[UnitView] = []
        for enemy in turn.visible_enemies:
            if not isinstance(enemy, UnitView):
                continue
            if enemy.unit_type not in {UnitType.VANGUARD, UnitType.RANGER}:
                continue
            if _distance(enemy.position, unit.position) <= COMET_THREAT_RADIUS:
                threats.append(enemy)
        return threats

    def _comet_ranger_engage(
        self,
        turn: Turn,
        planner: MovementPlanner,
        ranger: Ranger,
        threats: list[UnitView],
        *,
        ledger: ShotLedger,
        contacts_by_id: dict[UUID, ThreatContact],
        acted_units: set[UUID],
        decisions: list[str],
    ) -> bool:
        """彗星游侠遇敌交战：复用与 _choose_rangers_lightning 同源的格斗技巧。

        严格按 lightning 游侠的多级优先级开火，不另写简陋射原位逻辑：
          1) 游侠单杀先锋舞步(_find_vanguard_dance_target / _resolve_vanguard_dance
             状态机)：kiting 接战范围内有敌先锋时，先走 APPROACH_GAP→ADJACENT_BACK
             →REAIM_GAP_HP2→FLEE_AMBUSH 舞步，含后退诱空、预瞄撤退格、集群设伏。
          2) 打分制预瞄(_ranger_shot_candidates → _score_aim_cells →
             _predicted_enemy_cell → 7帧轨迹库 _enemy_motion_pattern)：对射程内
             敌方单位预测其下一格并瞄那格，按脱靶反馈降分。
        命中即计入 acted_units 并返回 True；射程内无合法射击时返回 False（由
        调用方决定原地待命或安全推进）。
        """
        # 1) 游侠单杀先锋舞步——优先于通用 legal 射击（与 lightning 同序）。
        dance_enemy = self._find_vanguard_dance_target(turn, ranger)
        if dance_enemy is not None and self._resolve_vanguard_dance(
            turn, planner, ranger, dance_enemy, ledger, decisions
        ):
            acted_units.add(ranger.id)
            return True
        # 2) 打分制预瞄：枚举射程内所有敌方单位的候选射击格，按打分取最优。
        candidates = self._ranger_shot_candidates(turn, ranger, planner)
        legal: list[tuple[UnitView | CoreView, Position, ThreatContact | None, bool]] = []
        for enemy, cell in candidates:
            predicted = cell != enemy.position
            if ledger.can_assign(enemy, predicted=predicted):
                legal.append((enemy, cell, contacts_by_id.get(enemy.id), predicted))
        if not legal:
            return False
        # 优先级与 lightning 一致：T3/T4 威胁 > 角色优先级(游侠>先锋>Core) >
        # 有效血量 > 原位优先(predicted=0) > UUID/cell 稳定决胜。
        def priority(item: tuple[UnitView | CoreView, Position, ThreatContact | None, bool]) -> tuple:
            enemy, cell, contact, predicted = item
            return (
                0 if contact and contact.tier in {"T4", "T3"} else 1,
                contact.core_eta if contact else 99,
                _enemy_role_priority(enemy),
                _effective_hp(enemy),
                1 if predicted else 0,
                enemy.id.bytes,
                cell,
            )
        enemy, cell, _, _ = min(legal, key=priority)
        on_blind_cell = ranger.position in self._enemy_blind_firing_cells(
            turn, enemy.position, planner.obstacles
        )
        ranger.shoot(enemy, expected_cell=cell)
        ledger.assign(ranger, enemy, cell)
        self._mark_ranger_shot(enemy, cell, blind=on_blind_cell)
        acted_units.add(ranger.id)
        decisions.append(
            f"ranger:{_short_id(ranger.id)} comet_engage "
            f"target={_short_id(enemy.id)} cell={cell} "
            f"predicted={cell != enemy.position}"
        )
        self.memory.decision_totals["comet:ranger:engage"] += 1
        self.memory.decision_totals["ranger:shot"] += 1
        return True

    def _comet_step_safe_toward(
        self,
        turn: Turn,
        planner: MovementPlanner,
        unit: Unit,
        goal: Position,
        reason: str,
    ) -> bool:
        """彗星成员向 goal 推进一步，但绝不走进敌方战斗单位的射程圈内。

        与 _lightning_step_toward 的区别：候选格只要"下一格距任何敌方战斗单位 ≤
        COMET_THREAT_RADIUS"就剔除，而不是仅在威胁项里加一个轻权重——这从根上
        杜绝了"距信标只差一格 → 距离项压过威胁项 → 一头扎进火力线"的送。
        打分仍以距 goal 为主，威胁作并列惩罚，使单位在安全格中挑离目标最近的那格。
        """
        if unit.position == goal:
            return False
        threats = self._comet_enemy_threats(turn, unit)
        threat_positions = [e.position for e in threats]
        candidates: list[tuple[float, int, Direction]] = []
        heading = self.memory.unit_headings.get(str(unit.id))
        for direction in DIRECTION_ORDER:
            destination = _destination(unit.position, direction)
            if (
                destination in planner.obstacles
                or destination in planner.enemy_cells
                or planner.final_occupancy(destination) >= 2
            ):
                continue
            # 威胁硬阻断：下一格距任何敌方战斗单位 ≤ 警戒圈 → 不走这格。
            if any(
                _distance(destination, tp) <= COMET_THREAT_RADIUS
                for tp in threat_positions
            ):
                continue
            # 方向惯性沿用 _lightning_step_toward：掉头重罚，保持无罚。
            if heading is None:
                heading_penalty = 0.0
            elif direction == heading:
                heading_penalty = 0.0
            elif direction == OPPOSITE_DIRECTION[heading]:
                heading_penalty = 12.0
            else:
                heading_penalty = 2.0
            score = (
                _distance(destination, goal)
                + planner.threat.get(destination, 0) * 4.0
                + heading_penalty
            )
            candidates.append((score, DIRECTION_RANK[direction], direction))
        if not candidates:
            return False
        _, _, best = min(candidates, key=lambda triple: (triple[0], triple[1]))
        return planner._queue(unit, best, reason, goal=goal)

    def _lightning_step_toward(
        self,
        turn: Turn,
        planner: MovementPlanner,
        unit: Unit,
        goal: Position,
        reason: str,
    ) -> bool:
        """轨道巡逻单步移动:Core 风格四邻打分 + 卡住检测/逃生模式,不走 A*。

        A*(_find_path)在乱石堆死角返回空 path、fallback 在两格间横跳;这里只评估
        四邻 4 个方向:离目标最近 + 不撞障碍/敌人 + 方向惯性(掉头重罚防横跳) +
        visited 重罚(死角反复蹭几次就被推去绕路)。

        鬼打墙治本——卡住检测+逃生:距目标尚远却连续在 ≤ESCAPE_DETECT_SPAN 的
        小范围内震荡(窗口 ESCAPE_DETECT_WINDOW 个位置,连续命中 TRIGGER_HITS 次)
        → 进入逃生模式 ESCAPE_DURATION_TICKS 个 tick:完全忽略目标方向,只往
        "开阔(邻格出口多) + 低 visited 密度"方向走,强制脱出障碍口袋;
        已远离震荡区域则提前结束。返回 True 表示已移动。
        """
        if unit.position == goal:
            return False
        uid = str(unit.id)
        heading = self.memory.unit_headings.get(uid)
        escape_until = self.memory.lightning_unit_escape_until.get(uid, 0)
        recent = self.memory.recent_positions.get(uid, [])
        if turn.tick >= escape_until:
            if escape_until:
                self.memory.lightning_unit_escape_until.pop(uid, None)
            # 卡住检测：距目标尚远（>死区,排除在角附近正常待命）却小范围震荡。
            # 主判据：窗口内任意格重访次数 ≥ 3（覆盖大环震荡，不只是 2 格 ping-pong）。
            # 停驻(采集/召回)后刚恢复移动的单位每步都在新格(count=1),不误伤。
            window = recent[-LIGHTNING_ESCAPE_DETECT_WINDOW:]
            if (
                len(window) >= LIGHTNING_ESCAPE_DETECT_WINDOW
                and _distance(unit.position, goal) > CORE_BEACON_HYSTERESIS
            ):
                revisit_counts = Counter(window)
                max_revisits = revisit_counts.most_common(1)[0][1] if revisit_counts else 0
                # 8 位置窗口内，某格出现 3+ 次 = 震荡（可能是 2 格横跳、4 格环、甚至 8 格大环）
                if max_revisits >= 3:
                    hits = self.memory.lightning_unit_stuck_counters.get(uid, 0) + 1
                    if hits >= LIGHTNING_ESCAPE_TRIGGER_HITS:
                        escape_until = turn.tick + LIGHTNING_ESCAPE_DURATION_TICKS
                        self.memory.lightning_unit_escape_until[uid] = escape_until
                        self.memory.lightning_unit_stuck_counters.pop(uid, None)
                        self.memory.decision_totals["lightning:escape_triggered"] += 1
                    else:
                        self.memory.lightning_unit_stuck_counters[uid] = hits
                else:
                    self.memory.lightning_unit_stuck_counters.pop(uid, None)
        elif recent and _distance(unit.position, recent[0]) > LIGHTNING_ESCAPE_DETECT_WINDOW:
            # 逃生中但已远离震荡区域 → 提前结束,恢复正常朝目标走。
            self.memory.lightning_unit_escape_until.pop(uid, None)
            escape_until = 0
        escaping = turn.tick < escape_until
        candidates: list[tuple[float, int, Direction]] = []
        for direction in DIRECTION_ORDER:
            destination = _destination(unit.position, direction)
            if (
                destination in planner.obstacles
                or destination in planner.enemy_cells
                or planner.final_occupancy(destination) >= 2
            ):
                continue
            if escaping:
                # 逃生打分:无视目标,只看"往开阔处走 + 避开走烂的区域"。
                # 惯性弱化:逃生本质是掉头出死胡同,掉头只轻罚打破平衡防抖。
                if heading is None or direction == heading:
                    heading_penalty = 0.0
                elif direction == OPPOSITE_DIRECTION[heading]:
                    heading_penalty = 2.0
                else:
                    heading_penalty = 1.0
                exits = sum(
                    1
                    for exit_direction in DIRECTION_ORDER
                    if _destination(destination, exit_direction)
                    not in planner.obstacles
                )
                # 逃生期 visited 惩罚：直接用目标格自身的 visited 重罚，不用 3×3 求和。
                # 3×3 求和的致命缺陷：横跳的相邻几格互相落在对方窗口里，density 被抹平，
                # 三格评分几乎相同 → 靠 DIRECTION_RANK 决胜 → 稳定横跳出不去。
                # 单格 visited × 3.0 让"刚踩过 45 次的格"比"没踩过的格"贵得多，
                # 足以压过 exits(5.0/出口) 的差异，强制单位走没走过的方向。
                visited_penalty = (
                    self.memory.visited.get(destination, 0)
                    * LIGHTNING_ESCAPE_VISITED_WEIGHT
                )
                # 朝 goal 的弱偏置：走此格后距 goal 的距离 vs 当前距 goal 的距离。
                # 朝 goal 走 → goal_delta<0(奖励)；背向 → >0(轻罚)。权重 1.0，只在
                # exits/visited 并列时起决胜，把脱困后的单位轻轻往自己巡逻半径弯，
                # 防止逃生一路往内圈钻。goal 是巡逻点位(在轨道方环上)，故"朝 goal"
                # 即"朝轨道"，不改变"逃生以开阔度/visited 为主导"的语义。
                goal_delta = _distance(destination, goal) - _distance(unit.position, goal)
                score = (
                    -exits * LIGHTNING_ESCAPE_EXIT_WEIGHT
                    + visited_penalty
                    + planner.threat.get(destination, 0) * 4.0
                    + heading_penalty
                    + goal_delta * LIGHTNING_ESCAPE_GOAL_BIAS_WEIGHT
                )
            else:
                # 方向惯性:掉头反向重罚,保持当前方向无罚,转弯轻罚。单位 1格/tick
                # 比 Core 快 4 倍,掉头罚比 Core(8.0) 更重才压得住死角横跳。
                if heading is None:
                    heading_penalty = 0.0
                elif direction == heading:
                    heading_penalty = 0.0
                elif direction == OPPOSITE_DIRECTION[heading]:
                    heading_penalty = 12.0
                else:
                    heading_penalty = 2.0
                target_distance = _distance(destination, goal)
                # visited 惩罚：相对密度(当前格 vs 四邻平均)，防止整条轨道饱和后失去区分度。
                # 整条巡逻轨道 visited 都是 50 时，局部死角被反复蹭到 55，相对密度 +5 仍会被惩罚。
                # 系数 0.8：相对差值通常 < 5，让它与距离项 ±1 同量级。
                # 钳制 [-4, +10]：防止全新区域(当前格 0、四邻 50)产生 -40 的大负数压过距离项。
                visited_count = self.memory.visited.get(destination, 0)
                neighbors_visited = [
                    self.memory.visited.get(_destination(destination, d), 0)
                    for d in DIRECTION_ORDER
                ]
                avg_visited = sum(neighbors_visited) / len(neighbors_visited) if neighbors_visited else 0
                relative_density = visited_count - avg_visited
                visited_penalty = max(-4.0, min(10.0, relative_density * 0.8))
                score = (
                    target_distance
                    + planner.threat.get(destination, 0) * 4.0
                    + heading_penalty
                    + visited_penalty
                )
            candidates.append((score, DIRECTION_RANK[direction], direction))
        if not candidates:
            return False
        _, _, best = min(candidates, key=lambda triple: (triple[0], triple[1]))
        move_reason = f"{reason}:escape" if escaping else reason
        return planner._queue(unit, best, move_reason, goal=goal)

    def _aggress_core_reinforcement_state(
        self,
        turn: Turn,
    ) -> tuple[bool, tuple[UnitView, ...]]:
        if True or turn.core is None:
            return False, ()
        threats = tuple(
            enemy
            for enemy in turn.visible_enemies
            if isinstance(enemy, UnitView)
            and enemy.unit_type in {UnitType.VANGUARD, UnitType.RANGER}
            and _distance(enemy.position, turn.core.position)
            <= AGGRESS_CORE_ALERT_RADIUS
        )
        active = (
            len(threats) >= AGGRESS_CORE_REINFORCEMENT_ENEMY_COUNT
            or turn.tick <= self.memory.core_reinforcement_until_tick
            or any(
                _distance(enemy.position, turn.core.position)
                <= CORE_EMERGENCY_THREAT_RADIUS
                for enemy in threats
            )
        )
        return active, threats

    def _choose_vanguards(
        self,
        turn: Turn,
        planner: MovementPlanner,
        acted_units: set[UUID],
        decisions: list[str],
    ) -> None:
        # lightning 是唯一活跃分支；recall/rally/aggress/beacon/migrate/develop
        # 等旧分支已下线（对应死函数一并删除）。
        self._choose_vanguards_lightning(turn, planner, acted_units, decisions)

    def _sweep_targets(
        self,
        vanguard: Vanguard,
        turn: Turn,
        *,
        include_core: bool = True,
        include_workers: bool = True,
    ) -> Direction | None:
        sweep_options: list[tuple[int, int, Direction]] = []
        for direction in DIRECTION_ORDER:
            target_cell = _destination(vanguard.position, direction)
            targets = [
                enemy
                for enemy in turn.visible_enemies
                if enemy.position == target_cell
                and (include_core or not isinstance(enemy, CoreView))
                and (
                    include_workers
                    or not isinstance(enemy, UnitView)
                    or enemy.unit_type is not UnitType.WORKER
                )
            ]
            if targets:
                weight = sum(
                    5 if isinstance(enemy, CoreView)
                    else 3 if enemy.unit_type is UnitType.RANGER
                    else 2 if enemy.unit_type is UnitType.VANGUARD
                    else 1
                    for enemy in targets
                )
                sweep_options.append((weight, len(targets), direction))
        if not sweep_options:
            return None
        return max(
            sweep_options,
            key=lambda item: (item[0], item[1], -DIRECTION_RANK[item[2]]),
        )[2]

    def _aggress_beacon_guard_assignments(
        self,
        turn: Turn,
        *,
        apply_rotations: bool = True,
    ) -> tuple[Vanguard | None, set[UUID], set[UUID]]:
        if (
            True
            or turn.beacon.status is not BeaconStatus.CARRIED
            or turn.beacon.carrier_id is None
        ):
            self.memory.aggress_beacon_guard_carrier_id = None
            self.memory.aggress_beacon_vanguard_guards.clear()
            self.memory.aggress_beacon_ranger_guards.clear()
            return None, set(), set()
        carrier = next(
            (
                vanguard
                for vanguard in turn.vanguards
                if vanguard.id == turn.beacon.carrier_id
            ),
            None,
        )
        if carrier is None:
            self.memory.aggress_beacon_guard_carrier_id = None
            self.memory.aggress_beacon_vanguard_guards.clear()
            self.memory.aggress_beacon_ranger_guards.clear()
            return None, set(), set()

        carrier_key = str(carrier.id)
        if self.memory.aggress_beacon_guard_carrier_id != carrier_key:
            self.memory.aggress_beacon_guard_carrier_id = carrier_key
            self.memory.aggress_beacon_vanguard_guards.clear()
            self.memory.aggress_beacon_ranger_guards.clear()

        def guard_priority(
            unit: Unit,
            sticky_ids: set[str],
        ) -> tuple[int, int, bytes]:
            distance = _distance(unit.position, carrier.position)
            return (
                0
                if str(unit.id) in sticky_ids
                and distance <= BEACON_GUARD_REASSIGN_RADIUS
                else 1,
                distance,
                unit.id.bytes,
            )

        stored_vanguard_guards = self.memory.aggress_beacon_vanguard_guards
        stored_ranger_guards = self.memory.aggress_beacon_ranger_guards
        vanguard_guards = sorted(
            (
                vanguard
                for vanguard in turn.vanguards
                if vanguard.id != carrier.id
            ),
            key=lambda unit: guard_priority(unit, stored_vanguard_guards),
        )[:BEACON_GUARD_VANGUARDS]
        ranger_guards = sorted(
            turn.rangers,
            key=lambda unit: guard_priority(unit, stored_ranger_guards),
        )[:BEACON_GUARD_RANGERS]
        vanguard_guard_ids = {unit.id for unit in vanguard_guards}
        ranger_guard_ids = {unit.id for unit in ranger_guards}
        if apply_rotations:
            replaced_vanguard_ids: set[UUID] = set()
            replaced_ranger_ids: set[UUID] = set()
            for patient, relief in self._aggress_heal_role_pairs(turn):
                if patient.id in vanguard_guard_ids:
                    vanguard_guard_ids.remove(patient.id)
                    vanguard_guard_ids.add(relief.id)
                    replaced_vanguard_ids.add(patient.id)
                elif patient.id in ranger_guard_ids:
                    ranger_guard_ids.remove(patient.id)
                    ranger_guard_ids.add(relief.id)
                    replaced_ranger_ids.add(patient.id)

            for unit in sorted(
                turn.vanguards,
                key=lambda candidate: guard_priority(
                    candidate,
                    stored_vanguard_guards,
                ),
            ):
                if len(vanguard_guard_ids) >= BEACON_GUARD_VANGUARDS:
                    break
                if (
                    unit.id != carrier.id
                    and unit.id not in vanguard_guard_ids
                    and unit.id not in replaced_vanguard_ids
                ):
                    vanguard_guard_ids.add(unit.id)
            for unit in sorted(
                turn.rangers,
                key=lambda candidate: guard_priority(
                    candidate,
                    stored_ranger_guards,
                ),
            ):
                if len(ranger_guard_ids) >= BEACON_GUARD_RANGERS:
                    break
                if (
                    unit.id not in ranger_guard_ids
                    and unit.id not in replaced_ranger_ids
                ):
                    ranger_guard_ids.add(unit.id)
        self.memory.aggress_beacon_vanguard_guards = {
            str(unit_id) for unit_id in vanguard_guard_ids
        }
        self.memory.aggress_beacon_ranger_guards = {
            str(unit_id) for unit_id in ranger_guard_ids
        }
        return carrier, vanguard_guard_ids, ranger_guard_ids

    def _beacon_guard_anchor(self, carrier: Vanguard, tick: int) -> Position:
        planned = self.memory.planned_moves.get(str(carrier.id))
        if planned is not None and planned.tick == tick:
            return planned.destination
        return carrier.position

    def _beacon_guard_slots(
        self,
        turn: Turn,
        planner: MovementPlanner,
        anchor: Position,
        guards: list[Unit],
        offsets: tuple[Position, ...],
        *,
        rotation: int = 0,
        evenly_spaced: bool = False,
    ) -> dict[UUID, Position]:
        slots: dict[UUID, Position] = {}
        reserved: set[Position] = set()
        for index, guard in enumerate(sorted(guards, key=_uuid_key)):
            start_index = index
            if evenly_spaced:
                start_index = (
                    rotation + index * len(offsets) // max(1, len(guards))
                )
            for offset_index in range(len(offsets)):
                dx, dy = offsets[(start_index + offset_index) % len(offsets)]
                position = anchor[0] + dx, anchor[1] + dy
                if (
                    position in reserved
                    or position in planner.obstacles
                    or position in planner.enemy_cells
                    or position in turn.resource_cells
                    or (
                        position != guard.position
                        and planner.final_occupancy(position) >= 2
                    )
                ):
                    continue
                slots[guard.id] = position
                reserved.add(position)
                break
        return slots

    def _choose_aggress_beacon_carrier(
        self,
        turn: Turn,
        planner: MovementPlanner,
        carrier: Vanguard,
        vanguard_guard_ids: set[UUID],
        ranger_guard_ids: set[UUID],
        vanguard_defender_ids: set[UUID],
        ranger_defender_ids: set[UUID],
        combat_target: Position | None,
        frontier_target: Position | None,
        acted_units: set[UUID],
        decisions: list[str],
    ) -> None:
        if carrier.id in acted_units:
            return

        core_avoid: set[Position] = set()
        if turn.core is not None:
            minimum_core_distance = min(
                BEACON_CARRIER_CORE_AVOID_RADIUS,
                _distance(carrier.position, turn.core.position),
            )
            for dx in range(-minimum_core_distance, minimum_core_distance + 1):
                dy_span = minimum_core_distance - abs(dx)
                for dy in range(-dy_span, dy_span + 1):
                    core_avoid.add(
                        (turn.core.position[0] + dx, turn.core.position[1] + dy)
                    )

        guard_ids = vanguard_guard_ids | ranger_guard_ids
        defender_ids = vanguard_defender_ids | ranger_defender_ids
        comet_busy_ids = self.memory.comet_member_ids | self.memory.comet_retreating_ids
        forward_allies = [
            unit
            for unit in (*turn.vanguards, *turn.rangers)
            if unit.id != carrier.id
            and unit.id not in defender_ids
            and str(unit.id) not in comet_busy_ids
            and (
                turn.core is None
                or _distance(unit.position, turn.core.position)
                > BEACON_CARRIER_CORE_AVOID_RADIUS
            )
        ]
        nearby_support = [
            unit
            for unit in forward_allies
            if _distance(unit.position, carrier.position)
            <= BEACON_CARRIER_SUPPORT_RADIUS
        ]

        if not nearby_support:
            if forward_allies:
                regroup = min(
                    forward_allies,
                    key=lambda ally: (
                        -sum(
                            _distance(ally.position, teammate.position)
                            <= BEACON_CARRIER_SUPPORT_RADIUS
                            for teammate in forward_allies
                        ),
                        _distance(carrier.position, ally.position),
                        0 if ally.id in guard_ids else 1,
                        ally.id.bytes,
                    ),
                )
                if planner.toward(
                    carrier,
                    regroup.position,
                    "beacon_carrier_regroup",
                    avoid=core_avoid,
                ):
                    acted_units.add(carrier.id)
                    decisions.append(
                        f"vanguard:{_short_id(carrier.id)} regroup "
                        f"ally={_short_id(regroup.id)} target={regroup.position} "
                        f"support=0 core_avoid={bool(core_avoid)}"
                    )
                    self.memory.decision_totals["beacon_carrier:regroup"] += 1
                    return

            threats = [
                enemy.position
                for enemy in turn.visible_enemies
                if _distance(enemy.position, carrier.position)
                <= BEACON_CARRIER_DANGER_RADIUS
            ]
            if threats and planner.flee_open(
                carrier,
                threats,
                turn.core.position if turn.core is not None else None,
                "beacon_carrier_isolated_escape",
                avoid=core_avoid,
            ):
                acted_units.add(carrier.id)
                decisions.append(
                    f"vanguard:{_short_id(carrier.id)} isolated_escape "
                    f"threats={len(threats)} core_avoid={bool(core_avoid)}"
                )
                self.memory.decision_totals["beacon_carrier:isolated_escape"] += 1
                return

            carrier.wait()
            acted_units.add(carrier.id)
            self.memory.planned_moves.pop(str(carrier.id), None)
            decisions.append(
                f"vanguard:{_short_id(carrier.id)} wait "
                "reason=beacon_carrier_wait_escort support=0"
            )
            self.memory.decision_totals["beacon_carrier:wait_escort"] += 1
            return

        direction = self._sweep_targets(carrier, turn)
        if direction is not None:
            carrier.sweep(direction)
            acted_units.add(carrier.id)
            decisions.append(
                f"vanguard:{_short_id(carrier.id)} sweep {direction.value} "
                f"reason=beacon_carrier_attack support={len(nearby_support)}"
            )
            self.memory.decision_totals["beacon_carrier:sweep"] += 1
            return

        targets = []
        for target in (combat_target, frontier_target):
            if target is None or target in targets:
                continue
            if (
                turn.core is not None
                and _distance(target, turn.core.position)
                <= BEACON_CARRIER_CORE_AVOID_RADIUS
            ):
                continue
            targets.append(target)
        if targets and planner.toward(
            carrier,
            targets[0],
            "beacon_carrier_attack_advance",
            avoid=core_avoid,
        ):
            acted_units.add(carrier.id)
            decisions.append(
                f"vanguard:{_short_id(carrier.id)} attack_advance "
                f"target={targets[0]} support={len(nearby_support)}"
            )
            self.memory.decision_totals["beacon_carrier:attack_advance"] += 1
            return

        carrier.wait()
        acted_units.add(carrier.id)
        self.memory.planned_moves.pop(str(carrier.id), None)
        decisions.append(
            f"vanguard:{_short_id(carrier.id)} wait "
            f"reason=beacon_carrier_no_target support={len(nearby_support)}"
        )
        self.memory.decision_totals["beacon_carrier:no_target"] += 1

    def _assault_frontier_target(
        self,
        turn: Turn,
        planner: MovementPlanner,
    ) -> Position | None:
        if turn.core is not None:
            origin = turn.core.position
        elif turn.units:
            origin = min(turn.units, key=_uuid_key).position
        else:
            return None

        if (
            self.memory.aggress_sweep_profile_version
            != ASSAULT_SWEEP_PROFILE_VERSION
        ):
            self.memory.aggress_sweep_profile_version = (
                ASSAULT_SWEEP_PROFILE_VERSION
            )
            self.memory.aggress_sweep_started_tick = turn.tick
            self.memory.aggress_sweep_step = 0
            self.memory.aggress_sweep_last_advance_tick = 0
        elif self.memory.aggress_sweep_started_tick <= 0:
            self.memory.aggress_sweep_started_tick = turn.tick
            self.memory.aggress_sweep_step = 0
            self.memory.aggress_sweep_last_advance_tick = 0

        radius_span = ASSAULT_SWEEP_MAX_RADIUS - ASSAULT_SWEEP_MIN_RADIUS
        half_turn = len(ASSAULT_SWEEP_SECTOR_OFFSETS) // 2
        cycle_steps = radius_span * 2 + half_turn * 2
        phase = self.memory.aggress_sweep_step % cycle_steps
        if phase <= radius_span:
            radius = ASSAULT_SWEEP_MIN_RADIUS + phase
        elif phase <= radius_span + half_turn:
            radius = ASSAULT_SWEEP_MAX_RADIUS
        elif phase <= radius_span * 2 + half_turn:
            radius = ASSAULT_SWEEP_MAX_RADIUS - (
                phase - radius_span - half_turn
            )
        else:
            radius = ASSAULT_SWEEP_MIN_RADIUS
        sector_index = phase % len(ASSAULT_SWEEP_SECTOR_OFFSETS)
        sign_x, sign_y = ASSAULT_SWEEP_SECTOR_OFFSETS[sector_index]
        if sign_x and sign_y:
            x_distance = radius // 2
            y_distance = radius - x_distance
        else:
            x_distance = radius if sign_x else 0
            y_distance = radius if sign_y else 0
        arc_anchor = (
            origin[0] + sign_x * x_distance,
            origin[1] + sign_y * y_distance,
        )

        candidates: set[Position] = set()
        for dx in range(-radius, radius + 1):
            dy = radius - abs(dx)
            for position in (
                (origin[0] + dx, origin[1] + dy),
                (origin[0] + dx, origin[1] - dy),
            ):
                if _distance(position, arc_anchor) <= 4:
                    candidates.add(position)
        candidates.difference_update(planner.obstacles)
        if not candidates:
            return None

        def score(position: Position) -> tuple[float, Position]:
            return (
                _distance(position, arc_anchor) * 8
                + planner.threat.get(position, 0) * 25
                + self.memory.visited.get(position, 0) * 3
                - _chunk_quota(_chunk_of(position)) * 0.2,
                position,
            )

        target = min(candidates, key=score)
        # 守家、信标和偷袭编队不参与前沿推进判定；只有主侵略队整体
        # 到达当前航点才换圈，避免召回中的独立编队永久卡住扫荡。
        carrier, beacon_vanguard_guard_ids, beacon_ranger_guard_ids = (
            self._aggress_beacon_guard_assignments(turn)
        )
        vanguard_defenders, ranger_defenders = self._aggress_core_defender_ids(turn)
        excluded_ids = (
            beacon_vanguard_guard_ids
            | beacon_ranger_guard_ids
            | vanguard_defenders
            | ranger_defenders
        )
        if carrier is not None:
            excluded_ids.add(carrier.id)
        comet_busy_ids = self.memory.comet_member_ids | self.memory.comet_retreating_ids
        assault_units = tuple(
            unit
            for unit in (*turn.vanguards, *turn.rangers)
            if unit.id not in excluded_ids
            and str(unit.id) not in comet_busy_ids
        )
        target_reached = bool(assault_units) and all(
            _distance(unit.position, target)
            <= ASSAULT_SWEEP_WAYPOINT_REACHED_RADIUS
            for unit in assault_units
        )
        if (
            target_reached
            and self.memory.aggress_sweep_last_advance_tick != turn.tick
        ):
            self.memory.aggress_sweep_step += 1
            self.memory.aggress_sweep_last_advance_tick = turn.tick
            return self._assault_frontier_target(turn, planner)
        return target

    def _enemy_motion_pattern(self, enemy: UnitView | CoreView) -> str:
        """从敌方 7 帧轨迹库识别运动规律：ZIGZAG / LINEAR / CIRCLE / UNKNOWN。

        - ZIGZAG：相邻帧方向频繁翻转（包围圈里来回绕）。
        - LINEAR：方向连续 ≥2 帧不回弹（直线逃离/推进）。
        - CIRCLE：位置绕某锚点形成闭合弧（风筝）。
        - UNKNOWN：轨迹不足或静止。
        轨迹库不足 3 帧时回退 UNKNOWN，调用方自行走原外推。
        """
        if isinstance(enemy, CoreView):
            return "UNKNOWN"
        trail = self.memory.enemy_trails.get(str(enemy.id), [])
        if len(trail) < 3:
            return "UNKNOWN"
        # 步进方向序列（每对相邻帧的 dx,dy）。
        steps: list[tuple[int, int]] = []
        for a, b in zip(trail, trail[1:]):
            dx, dy = b[0] - a[0], b[1] - a[1]
            steps.append((dx, dy))
        moves = [s for s in steps if s != (0, 0)]
        if len(moves) < 2:
            return "UNKNOWN"
        # 方向翻转次数。
        flips = 0
        for a, b in zip(moves, moves[1:]):
            if a[0] == -b[0] and a[1] == -b[1]:
                flips += 1
        if flips >= 2 and flips / max(1, len(moves) - 1) >= 0.5:
            return "ZIGZAG"
        # 方向连续不翻转 → 直线。
        straight_run = 1
        for a, b in zip(moves, moves[1:]):
            if a == b:
                straight_run += 1
            else:
                break
        if straight_run >= 3:
            return "LINEAR"
        # CIRCLE：围绕轨迹质心转向一致性高（叉积同号）。
        cx = sum(p[0] for p in trail) / len(trail)
        cy = sum(p[1] for p in trail) / len(trail)
        cross_signs = set()
        for i in range(1, len(trail) - 1):
            rx0, ry0 = trail[i - 1][0] - cx, trail[i - 1][1] - cy
            rx1, ry1 = trail[i + 1][0] - cx, trail[i + 1][1] - cy
            cross = rx0 * ry1 - ry0 * rx1
            if cross > 0:
                cross_signs.add(1)
            elif cross < 0:
                cross_signs.add(-1)
        if len(cross_signs) == 1 and len(trail) >= 4:
            return "CIRCLE"
        return "UNKNOWN"

    def _predicted_enemy_cell(
        self,
        turn: Turn,
        enemy: UnitView | CoreView,
    ) -> Position:
        """预判敌人下一 tick 位置：优先用 7 帧轨迹库识别规律，回退一阶外推。

        - ZIGZAG：先锋在包围圈里来回绕——外推永远指向它刚离开的格，
          这是"永远瞄错方向"的根因。改为外推到对轴（它正在绕向的一侧）。
        - LINEAR：沿原方向外推 1 格（逃命/推进）。
        - CIRCLE/UNKNOWN：回退一阶速度外推。
        """
        current = enemy.position
        if isinstance(enemy, CoreView):
            return current
        pattern = self._enemy_motion_pattern(enemy)
        prev = self.memory.enemy_prev.get(str(enemy.id))
        if prev is None or prev == current:
            return current
        dx = current[0] - prev[0]
        dy = current[1] - prev[1]
        cardinal = abs(dx) <= 1 and abs(dy) <= 1 and (dx == 0 or dy == 0) and (dx or dy)
        if pattern == "ZIGZAG":
            # 对轴外推：若最近一帧沿 x 轴移动，下一格大概率切到 y 轴方向。
            # 朝当前到锚点（Core/最近友方）的主轴投影方向走一格，而非继续 x。
            anchor = self._enemy_movement_anchor(turn, enemy)
            if anchor is not None:
                adx = anchor[0] - current[0]
                ady = anchor[1] - current[1]
                if abs(adx) >= abs(ady) and ady != 0:
                    step = 1 if ady > 0 else -1
                    return (current[0], current[1] + step)
                if abs(ady) > abs(adx) and adx != 0:
                    step = 1 if adx > 0 else -1
                    return (current[0] + step, current[1])
            # 无锚点时取垂直于最近移动方向的任一轴（先 y）。
            if dx != 0:
                return (current[0], current[1] + 1)
            if dy != 0:
                return (current[0] + 1, current[1])
        if pattern == "LINEAR" and cardinal:
            return (current[0] + dx, current[1] + dy)
        if not cardinal:
            return current
        return (current[0] + dx, current[1] + dy)

    def _enemy_movement_anchor(
        self,
        turn: Turn,
        enemy: UnitView | CoreView,
    ) -> Position | None:
        """返回敌方单位在当前可见信息下最可能靠近的友方锚点。"""
        if isinstance(enemy, CoreView):
            return None
        if turn.beacon.carrier_id is not None:
            carrier = next(
                (
                    unit
                    for unit in turn.units
                    if unit.id == turn.beacon.carrier_id
                ),
                None,
            )
            if (
                carrier is not None
                and _distance(enemy.position, carrier.position)
                <= BEACON_GUARD_THREAT_RADIUS
            ):
                return carrier.position
        if (
            turn.core is not None
            and _distance(enemy.position, turn.core.position)
            <= AGGRESS_CORE_ALERT_RADIUS
        ):
            return turn.core.position
        friendly_combat = (*turn.vanguards, *turn.rangers)
        if friendly_combat:
            nearest = min(
                friendly_combat,
                key=lambda unit: (_distance(enemy.position, unit.position), unit.id.bytes),
            )
            if _distance(enemy.position, nearest.position) <= 6:
                return nearest.position
        return None

    def _enemy_shot_hypotheses(
        self,
        turn: Turn,
        enemy: UnitView | CoreView,
        planner: MovementPlanner,
    ) -> tuple[Position, ...]:
        """生成当前格、速度外推格及一格封堵候选，并只保留可见可通行格。"""
        current = enemy.position
        ordered: list[Position] = []
        predicted = self._predicted_enemy_cell(turn, enemy)
        if predicted != current:
            ordered.append(predicted)
        ordered.append(current)
        if isinstance(enemy, UnitView):
            anchor = self._enemy_movement_anchor(turn, enemy)
            if anchor is not None:
                delta_x = _sign(anchor[0] - current[0])
                delta_y = _sign(anchor[1] - current[1])
                if abs(anchor[0] - current[0]) >= abs(anchor[1] - current[1]):
                    delta_y = 0
                else:
                    delta_x = 0
                if delta_x or delta_y:
                    ordered.append((current[0] + delta_x, current[1] + delta_y))
            for direction in DIRECTION_ORDER:
                ordered.append(_destination(current, direction))

        hypotheses: list[Position] = []
        for cell in ordered:
            if cell in hypotheses or cell in planner.obstacles:
                continue
            hypotheses.append(cell)
        return tuple(hypotheses)

    def _score_aim_cells(
        self,
        turn: Turn,
        enemy: UnitView | CoreView,
        planner: MovementPlanner,
        *,
        context: str = "default",  # "default" | "standoff_escape" | "vanguard_dance"
    ) -> list[tuple[Position, float]]:
        """对敌人候选下一格打分(越高越该瞄)。复用 _enemy_shot_hypotheses 枚举候选格,
        _enemy_motion_pattern/_predicted_enemy_cell/_enemy_movement_anchor 读运动,
        shot_miss_counts/axis_miss_counts 做脱靶降分。调用方取 max(score) 即预瞄格。

        打分原则(用户拍板):敌方 agent 控制、原地概率小,STAY 基线最低;满血游侠主动入
        弹道→锁原位换血;残血遇满血→后撤方向优先、上下次之、向前最低;相持→后撤优先
        (后方常有障碍);追击/先锋贴脸→向前高分;连续脱靶某方向→降分(有上限防永久放弃)。
        """
        hypotheses = self._enemy_shot_hypotheses(turn, enemy, planner)
        if not hypotheses:
            return []
        # 敌人最近一步方向(enemy_prev→position),用于打 BACKWARD/FORWARD/LATERAL/STAY 标签。
        eid = str(enemy.id)
        prev = self.memory.enemy_prev.get(eid)
        current = enemy.position
        step = (0, 0)
        if prev is not None:
            step = (current[0] - prev[0], current[1] - prev[1])
        # 场景判定。
        low_hp_flee = False
        ambush_stay = False
        approach_forward = False
        if isinstance(enemy, UnitView):
            # (a) 满血敌游侠主动入弹道(偷袭):hp2 且最近一步缩短了与某友方游侠距离。
            if enemy.unit_type is UnitType.RANGER and enemy.hp == 2 and prev is not None:
                for friendly in turn.rangers:
                    if _distance(prev, friendly.position) > _distance(current, friendly.position):
                        ambush_stay = True
                        break
            # (b) 残血敌遇满血我方游侠 → 预瞄后撤:游侠 hp1 / 先锋 hp2 / 满血工人(hp2)。
            if (enemy.unit_type is UnitType.RANGER and enemy.hp <= 1) or (
                enemy.unit_type is UnitType.VANGUARD and enemy.hp <= 2
            ) or (enemy.unit_type is UnitType.WORKER and enemy.hp >= 2):
                # 仅当确有满血友方游侠在附近时才算 flee 诱因。
                if any(r.hp == 2 and _distance(r.position, current) <= 5 for r in turn.rangers):
                    low_hp_flee = True
            # (d) 先锋贴脸 / 追击我方残血 → 向前高分:vanguard_dance context 或先锋上一步朝某友方。
            if context == "vanguard_dance":
                approach_forward = True
            elif enemy.unit_type is UnitType.VANGUARD and prev is not None:
                for friendly in (*turn.rangers, *turn.vanguards):
                    if _distance(prev, friendly.position) > _distance(current, friendly.position):
                        approach_forward = True
                        break

        # 选权重表(优先级:ambush 锁原位 > 残血 flee > 相持 > 追击/贴脸)。
        # 残血优先于追击:hp2 先锋虽在逼近,但其下一步大概率逃(用户原则:残血→后撤最高)。
        weights = dict(AIM_DIRECTION_WEIGHTS)
        if ambush_stay:
            weights["STAY"] = AIM_AMBUSH_STAY_SCORE  # 锁原位换血
        elif low_hp_flee:
            # (b) 残血遇满血:后撤优先、上下次之、向前最低。
            weights.update({"BACKWARD": 60.0, "LATERAL": 30.0, "FORWARD": 10.0, "STAY": 2.0})
        elif context == "standoff_escape":
            # (c) 相持:其他游侠预瞄逃跑方向,后撤优先(后方常有障碍)。
            weights.update({"BACKWARD": 55.0, "LATERAL": 28.0, "FORWARD": 12.0, "STAY": 3.0})
        elif approach_forward:
            # (d) 追击/先锋贴脸:向前高分。
            weights.update({"FORWARD": 55.0, "STAY": 20.0, "LATERAL": 25.0, "BACKWARD": 15.0})

        target_key = eid
        scored: list[tuple[Position, float]] = []
        for cell in hypotheses:
            # 方向标签(相对敌人最近一步)。
            tag = self._aim_direction_tag(current, cell, step)
            score = weights.get(tag, AIM_DIRECTION_WEIGHTS["STAY"])
            # 重复覆盖格偏好(保留 ranger:shot_coverage 语义)。
            if (target_key, cell) in self.memory.current_shot_cells:
                score += AIM_COVERAGE_BONUS
            # 脱靶降分(封顶防永久放弃某方向)。
            cell_miss = self.memory.shot_miss_counts.get(
                _shot_cell_key(enemy.id, cell), 0
            )
            axis_key = _shot_axis_key(enemy.id, current, cell)
            axis_miss = self.memory.axis_miss_counts.get(axis_key or "", 0) if axis_key else 0
            penalty = min(
                AIM_MISS_PENALTY_CAP,
                cell_miss * AIM_MISS_CELL_PENALTY + axis_miss * AIM_MISS_AXIS_PENALTY,
            )
            score -= penalty
            scored.append((cell, score))
        return scored

    @staticmethod
    def _aim_direction_tag(
        enemy_pos: Position, cell: Position, step: tuple[int, int]
    ) -> str:
        """射击格 cell 相对敌人当前格 + 最近一步的方向标签。

        STAY=原地; BACKWARD=与最近一步反向; FORWARD=同向; LATERAL=垂直轴。
        敌人未动(step=0)时非原位格记 LATERAL(横向候选),原位记 STAY。
        """
        dx = cell[0] - enemy_pos[0]
        dy = cell[1] - enemy_pos[1]
        if dx == 0 and dy == 0:
            return "STAY"
        sx, sy = step
        if sx == 0 and sy == 0:
            return "LATERAL"  # 敌未动:非原位格视为横向候选
        # 与最近一步点积判定。
        dot = dx * sx + dy * sy
        if dot > 0:
            return "FORWARD"
        if dot < 0:
            return "BACKWARD"
        return "LATERAL"

    def _ranger_shot_candidates(
        self,
        turn: Turn,
        ranger: Ranger,
        planner: MovementPlanner,
    ) -> list[tuple[UnitView | CoreView, Position]]:
        """返回 (敌人, 射击格) 候选,并协调同 Tick 的火力覆盖。射击格由打分制选定。"""
        candidates: list[tuple[UnitView | CoreView, Position]] = []
        for enemy in turn.visible_enemies:
            target_prefix = f"{enemy.id}|"
            target_has_recent_miss = any(
                shot_key.startswith(target_prefix) and miss_count > 0
                for shot_key, miss_count in self.memory.shot_miss_counts.items()
            )
            coverage_active = (
                self._predicted_enemy_cell(turn, enemy) != enemy.position
                or target_has_recent_miss
            )
            hypotheses = (
                self._enemy_shot_hypotheses(turn, enemy, planner)
                if coverage_active
                else (enemy.position,)
            )
            legal_cells = tuple(
                cell
                for cell in hypotheses
                if _is_legal_ranger_shot(ranger.position, cell, planner.obstacles)
            )
            if not legal_cells:
                continue
            # 打分制:对候选格打分取最高(平手按 legal_cells 序 + cell 决胜,保持确定性)。
            scored = self._score_aim_cells(turn, enemy, planner)
            score_by_cell = {cell: score for cell, score in scored}
            best_cell = max(
                legal_cells,
                key=lambda cell: (
                    score_by_cell.get(cell, 0.0),
                    -legal_cells.index(cell),
                    _cell_sort_key(cell),
                ),
            )
            candidates.append((enemy, best_cell))
        return candidates


    def _mark_ranger_shot(
        self,
        target: UnitView | CoreView,
        cell: Position,
        blind: bool = False,
    ) -> None:
        target_key = str(target.id)
        if cell != target.position or self.memory.shot_miss_counts.get(
            _shot_cell_key(target.id, cell),
            0,
        ):
            self.memory.decision_totals["ranger:shot_coverage"] += 1
        self.memory.current_shot_cells.add((target_key, cell))
        # 盲区射击：射击格落在敌方游侠/先锋视野盲区里（对峙换血位即盲区位）。
        # 单独累计供验收“卡视野盲区”机制是否真的在产出无伤命中。
        if blind:
            self.memory.decision_totals["ranger:blind_fire"] += 1
        # 按轴记录本次射击：射击格相对敌人当前格的主轴。ZIGZAG 围猎里，
        # 同一轴连续开枪却打不中，下一帧候选会偏向对轴（见 _ranger_shot_candidates）。
        axis_key = _shot_axis_key(target.id, target.position, cell)
        if axis_key is not None:
            self.memory.axis_miss_counts[axis_key] += 1
            self.memory.axis_miss_ticks[axis_key] = self.memory.last_tick
    def _choose_vanguards_defend(
        self,
        turn: Turn,
        planner: MovementPlanner,
        acted_units: set[UUID],
        decisions: list[str],
        *,
        eligible_ids: set[UUID] | None = None,
    ) -> None:
        for vanguard in sorted(turn.vanguards, key=_uuid_key):
            if vanguard.id in acted_units or (
                eligible_ids is not None and vanguard.id not in eligible_ids
            ):
                continue
            sweep_options: list[tuple[int, int, Direction]] = []
            for direction in DIRECTION_ORDER:
                target_cell = _destination(vanguard.position, direction)
                targets = [enemy for enemy in turn.visible_enemies if enemy.position == target_cell]
                if targets:
                    weight = sum(
                        5 if isinstance(enemy, CoreView)
                        else 3 if enemy.unit_type is UnitType.RANGER
                        else 2 if enemy.unit_type is UnitType.VANGUARD
                        else 1
                        for enemy in targets
                    )
                    sweep_options.append((weight, len(targets), direction))
            if sweep_options:
                _, _, direction = max(
                    sweep_options,
                    key=lambda item: (item[0], item[1], -DIRECTION_RANK[item[2]]),
                )
                vanguard.sweep(direction)
                decisions.append(
                    f"vanguard:{_short_id(vanguard.id)} sweep {direction.value} reason=max_weight"
                )
                self.memory.decision_totals["vanguard:sweep"] += 1
                continue

            if turn.core is not None:
                threatening = [
                    enemy
                    for enemy in turn.visible_enemies
                    if _distance(enemy.position, turn.core.position) <= 7
                ]
                if threatening:
                    target = min(
                        threatening,
                        key=lambda enemy: (
                            _enemy_role_priority(enemy),
                            _distance(vanguard.position, enemy.position),
                            enemy.id.bytes,
                        ),
                    )
                    planner.toward(vanguard, target.position, "intercept_core_threat")
                    continue
                if _distance(vanguard.position, turn.core.position) > 2:
                    planner.toward(vanguard, turn.core.position, "guard_core")

    def _choose_rangers(
        self,
        turn: Turn,
        planner: MovementPlanner,
        acted_units: set[UUID],
        decisions: list[str],
    ) -> None:
        # lightning 是唯一活跃分支；recall/rally/aggress/beacon/migrate/develop
        # 等旧分支已下线（对应死函数一并删除）。
        self._choose_rangers_lightning(turn, planner, acted_units, decisions)
    def _choose_rangers_defend(
        self,
        turn: Turn,
        planner: MovementPlanner,
        acted_units: set[UUID],
        decisions: list[str],
        *,
        eligible_ids: set[UUID] | None = None,
    ) -> None:
        assigned_damage: Counter[UUID] = Counter()
        idle: list[Ranger] = []
        ordered_rangers = sorted(
            (
                ranger
                for ranger in turn.rangers
                if eligible_ids is None or ranger.id in eligible_ids
            ),
            key=lambda ranger: (
                self.memory.unit_labels.get(
                    str(ranger.id),
                    UnitLabel(UnitType.RANGER.value, 1_000_000),
                ).number,
                ranger.id.bytes,
            ),
        )
        patrol_rangers = ordered_rangers[: min(CORE_PATROL_RANGER_COUNT, len(ordered_rangers))]
        patrol_ids = {ranger.id for ranger in patrol_rangers}
        patrol_slots = self._core_patrol_slots(
            turn,
            planner,
            patrol_rangers,
        )
        pursuit_targets = tuple(
            enemy
            for enemy in turn.visible_enemies
            if turn.core is None
            or _distance(enemy.position, turn.core.position)
            <= RANGER_DEFENSE_LEASH_RADIUS
        )
        if turn.core is not None and pursuit_targets:
            nearest = min(
                pursuit_targets,
                key=lambda enemy: (
                    _distance(enemy.position, turn.core.position),
                    _enemy_role_priority(enemy),
                    enemy.id.bytes,
                ),
            )
            positions = ",".join(
                f"({enemy.position[0]},{enemy.position[1]})"
                for enemy in sorted(
                    pursuit_targets,
                    key=lambda enemy: (
                        _distance(enemy.position, turn.core.position),
                        enemy.id.bytes,
                    ),
                )
            )
            decisions.append(
                f"core_patrol_alert count={len(pursuit_targets)} "
                f"nearest={_short_id(nearest.id)} "
                f"distance={_distance(nearest.position, turn.core.position)} "
                f"positions={positions}"
            )
            self.memory.decision_totals["core_patrol:alert"] += 1

        for ranger in sorted(
            ordered_rangers,
            key=lambda candidate: (
                0 if candidate.id in patrol_ids else 1,
                candidate.id.bytes,
            ),
        ):
            if ranger.id in acted_units:
                continue
            shot_candidates = self._ranger_shot_candidates(turn, ranger, planner)
            if not shot_candidates:
                idle.append(ranger)
                continue
            target, cell = min(
                shot_candidates,
                key=lambda pair: (
                    1 if assigned_damage[pair[0].id] >= _effective_hp(pair[0]) else 0,
                    0 if turn.core is not None and _distance(pair[0].position, turn.core.position) <= 5 else 1,
                    _enemy_role_priority(pair[0]),
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
                f"expected={cell} "
                f"role={'core_patrol' if ranger.id in patrol_ids else 'mobile'}"
            )
            self.memory.decision_totals["ranger:shoot"] += 1
            if ranger.id in patrol_ids:
                self.memory.decision_totals["core_patrol:shoot"] += 1

        for ranger in sorted(
            idle,
            key=lambda candidate: (
                0 if candidate.id in patrol_ids else 1,
                candidate.id.bytes,
            ),
        ):
            if pursuit_targets:
                target = min(
                    pursuit_targets,
                    key=lambda enemy: (
                        0 if turn.core is not None and _distance(enemy.position, turn.core.position) <= 5 else 1,
                        _enemy_role_priority(enemy),
                        _distance(ranger.position, enemy.position),
                        enemy.id.bytes,
                    ),
                )
                firing_cells = self._firing_cells(target.position, planner.obstacles)
                if turn.core is not None:
                    firing_cells = {
                        position
                        for position in firing_cells
                        if _distance(position, turn.core.position)
                        <= RANGER_DEFENSE_LEASH_RADIUS
                    }
                if firing_cells:
                    firing_cell = min(
                        firing_cells,
                        key=lambda position: (
                            planner.threat.get(position, 0),
                            _distance(ranger.position, position),
                            self.memory.visited.get(position, 0),
                            position,
                        ),
                    )
                    reason = (
                        "core_patrol_intercept"
                        if ranger.id in patrol_ids
                        else "seek_firing_line"
                    )
                    if planner.toward(ranger, firing_cell, reason):
                        if ranger.id in patrol_ids:
                            self.memory.decision_totals["core_patrol:intercept"] += 1
                        continue
            patrol_slot = patrol_slots.get(ranger.id)
            if patrol_slot is not None and ranger.position != patrol_slot:
                if planner.toward(
                    ranger,
                    patrol_slot,
                    "ranger_core_patrol",
                    avoid=(turn.core.position,) if turn.core is not None else (),
                ):
                    self.memory.decision_totals["core_patrol:move"] += 1
                    continue
            if turn.core is not None and _distance(ranger.position, turn.core.position) > 3:
                planner.toward(ranger, turn.core.position, "ranger_screen")

    def _lightning_orbit_geometry(self, turn: Turn) -> OrbitGeometry:
        """Derive every defensive boundary from the final shared lane assignment."""
        lanes = self._lightning_assign_shared_middle_lanes(turn)
        ranger_lanes = [lane[0] for uid, lane in lanes.items()
                        if uid in {str(r.id) for r in turn.rangers}]
        all_lanes = [lane[0] for lane in lanes.values()]
        # The shared Ranger/Worker lanes are the source of truth.  The Vanguard
        # orbit sits one lane gap inside the first shared lane, so it expands or
        # contracts with the assigned formation rather than a defense constant.
        provisional_gap = max(1, LIGHTNING_ORBIT_LANE_GAP_RADIUS[UnitType.RANGER])
        first_lane = min(all_lanes, default=LIGHTNING_NEAR_ORBIT_RADIUS + provisional_gap)
        r_vanguard = max(1, first_lane - provisional_gap)
        radii = sorted(set([r_vanguard, *all_lanes]))
        gaps = [right - left for left, right in zip(radii, radii[1:]) if right > left]
        gap = min(gaps) if gaps else provisional_gap
        r_ranger_inner = min(ranger_lanes, default=r_vanguard + gap)
        r_ranger_outer = max(ranger_lanes, default=r_ranger_inner)
        occupied_outer = max(all_lanes, default=r_ranger_outer)
        # Keep the threat envelopes strictly ordered: committed melee space, an
        # inner screen, the complete Ranger lane, then sensor warning space.
        r_commit = max(r_vanguard + 1, r_ranger_inner - max(1, gap // 2))
        r_screen = max(r_commit + 1, r_ranger_inner)
        r_sensor_outer = max(occupied_outer, r_ranger_outer + gap * (2 + math.isqrt(max(1, len(lanes) + len(turn.vanguards)))))
        return OrbitGeometry(r_vanguard, r_ranger_inner, r_ranger_outer,
                             r_sensor_outer, gap, r_commit, r_screen, lanes)

    @staticmethod
    def _lightning_sector(origin: Position, position: Position) -> str:
        dx, dy = position[0] - origin[0], position[1] - origin[1]
        if abs(dx) >= abs(dy):
            return "E" if dx >= 0 else "W"
        return "S" if dy >= 0 else "N"

    @staticmethod
    def _lightning_square_radius(origin: Position, position: Position) -> int:
        return max(abs(position[0] - origin[0]), abs(position[1] - origin[1]))

    def _lightning_analyze_threats(self, turn: Turn, geometry: OrbitGeometry) -> tuple[ThreatContact, ...]:
        if turn.core is None:
            return ()
        contacts: list[ThreatContact] = []
        for enemy in turn.visible_enemies:
            if not isinstance(enemy, UnitView) or enemy.unit_type not in {UnitType.VANGUARD, UnitType.RANGER}:
                continue
            radius = self._lightning_square_radius(turn.core.position, enemy.position)
            attack_range = 1 if enemy.unit_type is UnitType.VANGUARD else 3
            core_eta = max(0, _distance(enemy.position, turn.core.position) - attack_range)
            if core_eta <= 1 or radius <= geometry.r_commit:
                tier, next_boundary = "T4", 0
            elif radius <= geometry.r_screen:
                tier, next_boundary = "T3", geometry.r_commit
            elif radius <= geometry.r_ranger_outer:
                tier, next_boundary = "T2", geometry.r_screen
            elif radius <= geometry.r_sensor_outer:
                tier, next_boundary = "T1", geometry.r_ranger_outer
            else:
                tier, next_boundary = "T0", geometry.r_sensor_outer
            # r_inf measures ring penetration; reducing it one step per move is
            # the conservative ETA to the next defensive layer.
            next_layer_eta = max(0, radius - next_boundary)
            contacts.append(ThreatContact(
                enemy, tier, radius, core_eta, next_layer_eta,
                self._lightning_sector(turn.core.position, enemy.position),
            ))
        return tuple(sorted(contacts, key=lambda c: (c.core_eta, c.square_radius, _uuid_key(c.enemy))))

    def _lightning_sector_fire_position(self, core: Position, sector: str, radius: int) -> Position:
        offsets = {"E": (radius, 0), "W": (-radius, 0), "N": (0, -radius), "S": (0, radius)}
        dx, dy = offsets[sector]
        return core[0] + dx, core[1] + dy

    def _lightning_plan_triage(
        self, turn: Turn, planner: MovementPlanner, geometry: OrbitGeometry,
        threats: tuple[ThreatContact, ...],
    ) -> tuple[tuple[Vacancy, ...], tuple[ReliefAssignment, ...]]:
        if turn.core is None:
            return (), ()
        # The vacancy lasts until the patient can queue at home, heal, and return
        # to its sector.  A relief must beat both that medical gap and the next
        # layer ETA of the attackers; Manhattan alone misses detours around walls.
        pressure = min(
            (max(1, contact.next_layer_eta) for contact in threats if contact.tier in {"T3", "T4"}),
            default=10 ** 6,
        )
        core_queue = max(0, sum(unit.position == turn.core.position for unit in turn.units) - 1)
        vacancies: list[Vacancy] = []
        for ranger in turn.rangers:
            if ranger.hp != 1:
                continue
            sector = self._lightning_sector(turn.core.position, ranger.position)
            fire = self._lightning_sector_fire_position(turn.core.position, sector, geometry.r_ranger_inner)
            t_home = planner.eta(ranger, turn.core.position)
            t_queue = core_queue
            t_heal = 1
            t_return = _distance(turn.core.position, fire)
            vacancies.append(Vacancy(
                ranger.id, sector, t_home, t_queue, t_heal, t_return,
                t_home + t_queue + t_heal + t_return, fire,
            ))
        healthy = [r for r in turn.rangers if r.hp >= 2]
        reliefs: list[ReliefAssignment] = []
        reserved: set[UUID] = set()
        for vacancy in sorted(vacancies, key=lambda v: (v.t_medical_gap, v.ranger_id.bytes)):
            candidates: list[tuple[int, bytes, Ranger]] = []
            for ranger in healthy:
                if ranger.id in reserved:
                    continue
                own_radius = self._lightning_square_radius(turn.core.position, ranger.position)
                own_sector = self._lightning_sector(turn.core.position, ranger.position)
                # Do not peel the final committed inner guard from another sector.
                if own_sector != vacancy.sector and own_radius <= geometry.r_commit:
                    continue
                eta = planner.eta(ranger, vacancy.fire_position)
                candidates.append((eta, ranger.id.bytes, ranger))
            if not candidates:
                continue
            eta, _, ranger = min(candidates)
            if eta < vacancy.t_medical_gap and eta <= pressure:
                reserved.add(ranger.id)
                reliefs.append(ReliefAssignment(ranger.id, vacancy.ranger_id, eta, vacancy.fire_position))
        return tuple(vacancies), tuple(reliefs)

    def _lightning_plan_funnel(self, turn: Turn, planner: MovementPlanner,
                               geometry: OrbitGeometry, threats: tuple[ThreatContact, ...]) -> FunnelPlan:
        if turn.core is None or not any(c.tier in {"T3", "T4"} for c in threats):
            return FunnelPlan()
        core = turn.core.position
        candidates: list[Position] = []
        for contact in threats:
            if contact.tier not in {"T3", "T4"}:
                continue
            for direction in DIRECTION_ORDER:
                cell = _destination(contact.enemy.position, direction)
                if cell in planner.obstacles or _distance(cell, core) >= _distance(contact.enemy.position, core):
                    continue
                if cell not in candidates:
                    candidates.append(cell)
        candidates = [cell for cell in candidates if cell not in planner.obstacles and _distance(cell, core) > 0]
        if not candidates:
            return FunnelPlan()
        ready_rangers = [r for r in turn.rangers if r.hp >= 2]
        def covered(cell: Position) -> int:
            return sum(_is_legal_ranger_shot(r.position, cell, planner.obstacles) for r in ready_rangers)
        # A gate is deliberately left open only when a healthy Ranger can cover it.
        # Without that shot, keeping an arbitrary path open is worse than closing
        # every immediately reachable Core route with the available Workers.
        coverage = {cell: covered(cell) for cell in candidates}
        gate = max(candidates, key=lambda c: (coverage[c], -_distance(c, core), c))
        if coverage[gate] <= 0:
            gate = None
            blocks = tuple(candidates)
        else:
            blocks = tuple(cell for cell in candidates if cell != gate)
        worker_pool = [w for w in turn.workers if not w.cargo]
        if any(c.tier == "T4" for c in threats):
            worker_pool = list(turn.workers)
        assignments: list[tuple[UUID, Position]] = []
        available = list(worker_pool)
        for cell in blocks:
            choices = [(planner.eta(worker, cell), worker.id.bytes, worker)
                       for worker in available]
            if not choices:
                break
            eta, _, worker = min(choices)
            if eta <= max(1, min(c.core_eta for c in threats if c.tier in {"T3", "T4"})):
                assignments.append((worker.id, cell))
                available.remove(worker)
        return FunnelPlan(gate, blocks, tuple(assignments), max(0, len(blocks) - len(assignments)))

    def _lightning_anchor_state(self, turn: Turn, threats: tuple[ThreatContact, ...],
                                vacancies: tuple[Vacancy, ...], funnel: FunnelPlan) -> CoreAnchorState:
        if any(c.tier in {"T3", "T4"} or c.core_eta <= 2 for c in threats) or funnel.block_cells or funnel.shortfall:
            return CoreAnchorState.COMBAT_ANCHOR
        # A Core step takes four Ticks; do not strand a Ranger which will arrive during it.
        if any(v.t_home <= 4 for v in vacancies):
            return CoreAnchorState.MEDICAL_ANCHOR
        return CoreAnchorState.MOBILE_EVADE

    def _lightning_prepare_plan(self, turn: Turn, planner: MovementPlanner, decisions: list[str]) -> LightningPlan:
        geometry = self._lightning_orbit_geometry(turn)
        threats = self._lightning_analyze_threats(turn, geometry)
        vacancies, reliefs = self._lightning_plan_triage(turn, planner, geometry, threats)
        funnel = self._lightning_plan_funnel(turn, planner, geometry, threats)
        anchor = self._lightning_anchor_state(turn, threats, vacancies, funnel)
        decisions.append(f"orbital geometry v={geometry.r_vanguard} r={geometry.r_ranger_inner}-{geometry.r_ranger_outer} sensor={geometry.r_sensor_outer} commit={geometry.r_commit} screen={geometry.r_screen} threats={','.join(c.tier for c in threats) or 'T0'} anchor={anchor.value}")
        self.memory.decision_totals[f"lightning:anchor:{anchor.value}"] += 1
        return LightningPlan(geometry, threats, vacancies, reliefs, funnel, anchor)

    def _lightning_execute_funnel_workers(self, turn: Turn, planner: MovementPlanner,
                                          acted_units: set[UUID], decisions: list[str]) -> None:
        plan = self._lightning_plan
        if plan is None:
            return
        for worker_id, cell in plan.funnel.assignments:
            worker = next((w for w in turn.workers if w.id == worker_id), None)
            if worker is None or worker.id in acted_units:
                continue
            if worker.position != cell and not planner.toward(worker, cell, "worker_funnel_block"):
                worker.wait()
            acted_units.add(worker.id)
            decisions.append(f"worker:{_short_id(worker.id)} funnel_block cell={cell} gate={plan.funnel.gate_cell}")
            self.memory.decision_totals["worker:funnel_block"] += 1

    def _lightning_safe_firing_position(self, turn: Turn, planner: MovementPlanner,
                                        ranger: Ranger, target: Position, sector: str,
                                        gate: Position | None,
                                        claimed: set[Position] | None = None) -> Position:
        # Candidate firing squares preserve a 2-3 cell ray, avoid melee adjacency,
        # visible enemy Ranger rays, and select cells which allied Rangers can cross-cover.
        # claimed: 本 tick 同 sector 其他游侠已认领的射击格(防扎堆贴墙根因)。
        # 已认领格在排序中重罚,使同 sector 多名游侠散到不同射击位而非挤一个格。
        candidates: list[Position] = []
        focus = gate or target
        for dx, dy in RANGER_LINE_DELTAS:
            for distance in (2, 3):
                cell = (focus[0] - dx * distance, focus[1] - dy * distance)
                if cell in planner.obstacles or cell in planner.enemy_cells:
                    continue
                if any(_distance(cell, enemy.position) <= 1 for enemy in turn.visible_enemies
                       if isinstance(enemy, UnitView) and enemy.unit_type is UnitType.VANGUARD):
                    continue
                if any(_is_legal_ranger_shot(enemy.position, cell, planner.obstacles)
                       for enemy in turn.visible_enemies
                       if isinstance(enemy, UnitView) and enemy.unit_type is UnitType.RANGER):
                    continue
                support = sum(_is_legal_ranger_shot(other.position, focus, planner.obstacles)
                              for other in turn.rangers if other.id != ranger.id)
                candidates.append((cell, support))
        if not candidates:
            return self._lightning_sector_fire_position(turn.core.position, sector, self._lightning_plan.geometry.r_ranger_inner)  # type: ignore[union-attr]
        claimed = claimed or set()
        return min(candidates, key=lambda item: (
            1 if item[0] in claimed else 0,           # 已被同 sector 友军认领 → 重罚排到后面
            _distance(ranger.position, item[0]), -item[1], item[0]
        ))[0]

    def _choose_vanguards_lightning(
        self, turn: Turn, planner: MovementPlanner, acted_units: set[UUID], decisions: list[str],
    ) -> None:
        if turn.core is None:
            return
        plan = self._lightning_plan or self._lightning_prepare_plan(turn, planner, decisions)
        urgent = [c for c in plan.threats if c.tier in {"T3", "T4"}]
        for vanguard in sorted(turn.vanguards, key=_uuid_key):
            if vanguard.id in acted_units:
                continue
            if self._comet_step_unit(
                turn, planner, vanguard, self._comet_target_tick,
                acted_units, decisions,
            ):
                continue
            sweep = self._sweep_targets(vanguard, turn)
            if sweep is not None:
                vanguard.sweep(sweep)
                decisions.append(f"vanguard:{_short_id(vanguard.id)} sweep priority=adjacent")
                self.memory.decision_totals["lightning:vanguard_sweep"] += 1
                acted_units.add(vanguard.id)
                continue
            if urgent:
                contact = min(urgent, key=lambda c: (_distance(vanguard.position, c.enemy.position), c.core_eta, c.enemy.id.bytes))
                own_sector = self._lightning_sector(turn.core.position, vanguard.position)
                sector_has_guard = any(
                    other.id != vanguard.id
                    and self._lightning_sector(turn.core.position, other.position) == contact.sector
                    for other in turn.vanguards
                )
                # Keep an opposite patrol in place whenever its own sector has
                # not been abandoned; cross-sector response is the fallback.
                if own_sector != contact.sector and sector_has_guard:
                    orbit = self._lightning_orbit_waypoint(turn, vanguard, UnitType.VANGUARD)
                    if orbit is not None and not self._lightning_step_toward(turn, planner, vanguard, orbit, "vanguard_hold_opposite_sector"):
                        vanguard.wait()
                    decisions.append(f"vanguard:{_short_id(vanguard.id)} hold sector={own_sector} threat_sector={contact.sector}")
                    acted_units.add(vanguard.id)
                    continue
                # COMMITTED Vanguards move to the enemy-Core intercept/funnel, never
                # retreat behind Core while the contact can still breach.
                goal = plan.funnel.gate_cell or self._lightning_sector_fire_position(turn.core.position, contact.sector, plan.geometry.r_vanguard)
                if _distance(contact.enemy.position, turn.core.position) <= plan.geometry.r_commit:
                    plan.committed_vanguards.add(vanguard.id)
                    if _distance(vanguard.position, goal) > 0:
                        self._lightning_step_toward(turn, planner, vanguard, goal, "vanguard_committed_intercept")
                    else:
                        vanguard.wait()
                    decisions.append(f"vanguard:{_short_id(vanguard.id)} COMMITTED sector={contact.sector} goal={goal}")
                else:
                    self._lightning_step_toward(turn, planner, vanguard, goal, "vanguard_screen_intercept")
                acted_units.add(vanguard.id)
                continue
            # 机会主义敌方 Core 猎杀分支已废弃:LIGHTNING_HUNT_MAX_DISTANCE=900≈全图 +
            # sighting 保留 300 tick + 释放条件只看视野内守卫,会让先锋被远方入雾的
            # 敌 Core 拽走几百格、脱离近轨数百 tick。先锋现在只守近轨(下方局部威胁
            # 退避 + 近轨巡逻)。
            if self._lightning_has_local_threat(turn, vanguard):
                if not self._lightning_step_toward(turn, planner, vanguard, turn.core.position, "lightning_retreat_local_threat"):
                    vanguard.wait()
                acted_units.add(vanguard.id)
                continue
            orbit = self._lightning_orbit_waypoint(turn, vanguard, UnitType.VANGUARD)
            if orbit is not None and not self._lightning_step_toward(turn, planner, vanguard, orbit, "lightning_vanguard_orbit"):
                vanguard.wait()
            acted_units.add(vanguard.id)

    def _lightning_standoff_enemy(self, turn: Turn) -> UnitView | None:
        """对峙僵局中的敌方游侠(薄包装):委托 _detect_strategic_standoff,
        仅当 kind=="ranger_ranger" 时返回该敌游侠,保持既有测试契约。
        """
        standoff = self._detect_strategic_standoff(turn)
        if standoff is None or standoff.kind != "ranger_ranger":
            return None
        return standoff.enemy

    def _detect_strategic_standoff(self, turn: Turn) -> StrategicStandoff | None:
        """泛化战略相持检测:不止游侠-游侠互瞄,还包括被围的低血敌先锋、被堵的逃命敌工人。
        任一检出即触发 45°支援换血(用户原则:主动触发、低限制、常用)。

        返回 StrategicStandoff(original_cell = 敌当前格 = 支援游侠要瞄的格)。
        """
        friendly_positions = [u.position for u in turn.rangers] + [u.position for u in turn.vanguards]
        for enemy in sorted(turn.visible_enemies, key=lambda e: e.id.bytes):
            if not isinstance(enemy, UnitView):
                continue
            current = enemy.position
            # (a) ranger_ranger:敌游侠近4帧在2x2盒原地 + 有友方游侠≤4格。
            if enemy.unit_type is UnitType.RANGER:
                trail = self.memory.enemy_trails.get(str(enemy.id), [])
                if trail:
                    recent = trail[-4:]
                    xs = [p[0] for p in recent]
                    ys = [p[1] for p in recent]
                    if max(xs) - min(xs) <= 2 and max(ys) - min(ys) <= 2:
                        if any(
                            _distance(unit.position, current) <= 4
                            for unit in turn.rangers
                        ):
                            return StrategicStandoff(enemy, "ranger_ranger", current)
            # (b) vanguard_cornered:敌先锋 hp≤2 + 4 cardinal 中≥2 被障碍/友方占据。
            if enemy.unit_type is UnitType.VANGUARD and enemy.hp <= 2:
                boxed = sum(
                    1
                    for direction in DIRECTION_ORDER
                    if self._cell_blocked_by_obstacle_or_friendly(
                        _destination(current, direction), friendly_positions, turn, planner=None
                    )
                )
                if boxed >= 2:
                    return StrategicStandoff(enemy, "vanguard_cornered", current)
            # (c) worker_fleeing:敌工人满血(hp2) + 最后一步远离我 Core/anchor + 逃跑受限。
            if enemy.unit_type is UnitType.WORKER and enemy.hp >= 2:
                trail = self.memory.enemy_trails.get(str(enemy.id), [])
                if len(trail) >= 2:
                    prev = trail[-2]
                    # 最后一步远离我 Core。
                    core_pos = turn.core.position if turn.core is not None else None
                    fleeing = (
                        core_pos is not None
                        and _distance(prev, core_pos) < _distance(current, core_pos)
                    )
                    if fleeing:
                        # 逃跑受限:4 cardinal ≥2 被堵,或有友游侠≤3格卡撤退轴。
                        boxed = sum(
                            1
                            for direction in DIRECTION_ORDER
                            if self._cell_blocked_by_obstacle_or_friendly(
                                _destination(current, direction), friendly_positions, turn, planner=None
                            )
                        )
                        ranger_near = any(
                            _distance(r.position, current) <= 3 for r in turn.rangers
                        )
                        if boxed >= 2 or ranger_near:
                            return StrategicStandoff(enemy, "worker_fleeing", current)
        return None

    def _cell_blocked_by_obstacle_or_friendly(
        self,
        cell: Position,
        friendly_positions: list[Position],
        turn: Turn,
        *,
        planner: MovementPlanner | None,
    ) -> bool:
        """该格是否被障碍或友方战斗单位占据(用于判定敌是否被围/逃跑受限)。
        planner 可为 None——此时用 memory.known_obstacles + 当前友方位作近似。
        """
        obstacles = (
            planner.obstacles
            if planner is not None
            else (set(self.memory.known_obstacles) | set(turn.obstacle_cells))
        )
        if cell in obstacles:
            return True
        return cell in friendly_positions

    def _pick_diagonal_support_ranger(
        self,
        turn: Turn,
        standoff: StrategicStandoff,
        planner: MovementPlanner,
        *,
        acted_units: set[UUID],
        vacancy_by_id: dict,
        relay_cell: Position | None,
        exclude_ids: set[UUID],
    ) -> Ranger | None:
        """挑满血、未参战、离对峙点最近且未被指派的游侠做 45°支援。

        放宽既有 _distance>3 限制(用户:低限制)——只要不在对峙点本身、未被 ShotLedger
        占用即可。优先调第三方游侠(不已在互瞄环 exclude_ids 内的),环外无可用则允许
        任意满血游侠。盲区位优先、无盲区直接取最远对角(_standoff_relay_cell 已落地)。
        """
        if relay_cell is None:
            return None
        target = standoff.original_cell

        def eligible(ranger: Ranger) -> bool:
            return (
                ranger.hp == 2
                and ranger.id not in acted_units
                and ranger.id not in vacancy_by_id
                and ranger.id not in exclude_ids
                and ranger.id not in self.memory.standoff_support_assigned
                and str(ranger.id) not in self.memory.standoff_support_cooldown
                and ranger.position != target
            )

        all_rangers = [r for r in turn.rangers if eligible(r)]
        if not all_rangers:
            return None
        # 优先选不在互瞄环内的第三方(打破僵局);无则任意满血(低限制)。
        third_party = [r for r in all_rangers if r.id not in exclude_ids]
        pool = third_party or all_rangers
        chosen = min(
            pool,
            key=lambda r: (_distance(r.position, relay_cell), r.id.bytes),
        )
        self.memory.standoff_support_assigned.add(str(chosen.id))
        return chosen

    def _standoff_relay_cell(
        self,
        turn: Turn,
        standoff: UnitView,
        planner: MovementPlanner,
    ) -> Position | None:
        """换血位：敌游侠盲区中距离 3 对角格优先；无盲区回退距离 3 直线格。

        无盲区时换血依然成立（用户原则）：站在对方弹道上，对方也不能动
        ——其他位置已被我方瞄准，它动一下就会被射。
        """
        target = standoff.position
        obstacles = planner.obstacles
        blind = self._enemy_blind_firing_cells(turn, target, obstacles)
        pool = blind or self._firing_cells(target, obstacles)
        diagonal_far = [
            cell
            for cell in pool
            if abs(cell[0] - target[0]) == 3 and abs(cell[1] - target[1]) == 3
        ]
        if diagonal_far:
            return min(diagonal_far, key=lambda cell: cell)
        straight_far = [
            cell
            for cell in pool
            if max(abs(cell[0] - target[0]), abs(cell[1] - target[1])) == 3
        ]
        if straight_far:
            return min(straight_far, key=lambda cell: cell)
        return None

    def _find_vanguard_dance_target(
        self, turn: Turn, ranger: Ranger
    ) -> UnitView | None:
        """挑该游侠 kiting 接战范围(VANGUARD_DANCE_ENGAGE_RADIUS)内最近的敌先锋。
        优先选已在 vanguard_dance_phase 跟踪的(延续舞步),否则挑最近的新目标。"""
        tracked_enemy_ids = {
            key.split("|", 1)[1]
            for key in self.memory.vanguard_dance_phase
            if key.split("|", 1)[0] == str(ranger.id)
        }
        in_range = [
            enemy
            for enemy in turn.visible_enemies
            if isinstance(enemy, UnitView)
            and enemy.unit_type is UnitType.VANGUARD
            and _distance(ranger.position, enemy.position) <= VANGUARD_DANCE_ENGAGE_RADIUS
        ]
        if not in_range:
            return None
        # 优先延续已跟踪的。
        tracked = [e for e in in_range if str(e.id) in tracked_enemy_ids]
        pool = tracked or in_range
        return min(pool, key=lambda e: (_distance(ranger.position, e.position), e.id.bytes))

    def _resolve_vanguard_dance(
        self,
        turn: Turn,
        planner: MovementPlanner,
        ranger: Ranger,
        enemy_vanguard: UnitView,
        ledger: ShotLedger,
        decisions: list[str],
    ) -> bool:
        """游侠 vs 敌先锋单杀舞步(4 阶段状态机)。返回 True=本游侠已处理。

        APPROACH_GAP(距2预瞄中间格) → ADJACENT_BACK(贴脸hp≥3后退诱空) →
        REAIM_GAP_HP2(hp2预瞄撤退格) → FLEE_AMBUSH(敌反攻我掉hp1逃向集群+集群设伏)。
        在 _choose_rangers_lightning 的 legal 射击分支之前介入。
        """
        pair_key = f"{ranger.id}|{enemy_vanguard.id}"
        phase_state = self.memory.vanguard_dance_phase.get(pair_key)
        # 无跟踪态时按距离决定是否启动舞步。
        phase = phase_state["phase"] if phase_state else None
        dist = _distance(ranger.position, enemy_vanguard.position)

        # FLEE_AMBUSH:上一 tick 处于 REAIM_GAP_HP2,本 tick 我游侠掉到 hp1 且先锋仍贴脸可见。
        if phase == "REAIM_GAP_HP2" and ranger.hp <= 1:
            phase = "FLEE_AMBUSH"

        # 启动判定:无 phase 且距离=2 且先锋上一步朝我 → APPROACH_GAP。
        if phase is None and dist == 2:
            prev = self.memory.enemy_prev.get(str(enemy_vanguard.id))
            if prev is not None and _distance(prev, ranger.position) > dist:
                phase = "APPROACH_GAP"

        if phase is None:
            return False

        # 写回 phase_state(更新 hp/prev 记录)。
        self.memory.vanguard_dance_phase[pair_key] = {
            "phase": phase,
            "enemy_hp_last": enemy_vanguard.hp,
            "enemy_prev_cell": self.memory.enemy_prev.get(str(enemy_vanguard.id)),
            "tick": turn.tick,
        }

        if phase == "APPROACH_GAP":
            # 预瞄中间格(先锋贴脸必经格)。
            gap = self._vanguard_dance_gap_cell(ranger, enemy_vanguard)
            if gap is not None and _is_legal_ranger_shot(
                ranger.position, gap, planner.obstacles
            ):
                if ledger.can_assign(enemy_vanguard, predicted=True):
                    ranger.shoot(enemy_vanguard, expected_cell=gap)
                    ledger.assign(ranger, enemy_vanguard, gap)
                    self._mark_ranger_shot(enemy_vanguard, gap, blind=False)
                    decisions.append(
                        f"ranger:{_short_id(ranger.id)} vanguard_dance APPROACH_GAP "
                        f"target={_short_id(enemy_vanguard.id)} gap={gap}"
                    )
                    self.memory.decision_totals["ranger:vanguard_dance"] += 1
                    self.memory.decision_totals["ranger:shot"] += 1
                    self.memory.vanguard_dance_phase[pair_key]["phase"] = "ADJACENT_BACK"
                    return True
            return False

        if phase == "ADJACENT_BACK":
            # 贴脸且先锋 hp≥3 → 其下一动作必进攻,我后退一格让其扑空。
            if dist <= 1 and enemy_vanguard.hp >= 3:
                back_dir = self._vanguard_dance_back_direction(ranger, enemy_vanguard)
                if back_dir is not None and self._can_ranger_move_to(
                    turn, planner, ranger, back_dir
                ):
                    ranger.move(back_dir)
                    decisions.append(
                        f"ranger:{_short_id(ranger.id)} vanguard_dance ADJACENT_BACK "
                        f"retreat={back_dir}"
                    )
                    self.memory.decision_totals["ranger:vanguard_dance"] += 1
                    self.memory.vanguard_dance_phase[pair_key]["phase"] = "REAIM_GAP_HP2"
                    return True
                # 被卡 → 回退向 Core。
                if turn.core is not None and planner.toward(
                    ranger, turn.core.position, "vanguard_dance_retreat"
                ):
                    decisions.append(
                        f"ranger:{_short_id(ranger.id)} vanguard_dance ADJACENT_BACK retreat_core"
                    )
                    self.memory.decision_totals["ranger:vanguard_dance"] += 1
                    self.memory.vanguard_dance_phase[pair_key]["phase"] = "REAIM_GAP_HP2"
                    return True
            # 先锋已掉到 hp2 或离开贴脸 → 跳到 REAIM_GAP_HP2。
            self.memory.vanguard_dance_phase[pair_key]["phase"] = "REAIM_GAP_HP2"
            return False

        if phase == "REAIM_GAP_HP2":
            # 先锋 hp2 → 预判逃跑,预瞄其撤退格(低血 flee 权重已优先 BACKWARD)。
            if enemy_vanguard.hp <= 2:
                scored = self._score_aim_cells(
                    turn, enemy_vanguard, planner, context="default"
                )
                legal_scored = [
                    (cell, score)
                    for cell, score in scored
                    if _is_legal_ranger_shot(
                        ranger.position, cell, planner.obstacles
                    )
                ]
                if legal_scored and ledger.can_assign(enemy_vanguard, predicted=True):
                    cell = max(
                        legal_scored,
                        key=lambda cs: (cs[1], _cell_sort_key(cs[0])),
                    )[0]
                    ranger.shoot(enemy_vanguard, expected_cell=cell)
                    ledger.assign(ranger, enemy_vanguard, cell)
                    self._mark_ranger_shot(enemy_vanguard, cell, blind=False)
                    decisions.append(
                        f"ranger:{_short_id(ranger.id)} vanguard_dance REAIM_GAP_HP2 "
                        f"target={_short_id(enemy_vanguard.id)} cell={cell}"
                    )
                    self.memory.decision_totals["ranger:vanguard_dance"] += 1
                    self.memory.decision_totals["ranger:shot"] += 1
                    return True
            # 先锋没掉到 hp2(仍 hp3+)或无合法撤退格 → 留在 ADJACENT_BACK 继续。
            self.memory.vanguard_dance_phase[pair_key]["phase"] = "ADJACENT_BACK"
            return False

        if phase == "FLEE_AMBUSH":
            # 我游侠掉到 hp1:逃向集群(最近友游侠/Core)。
            cluster_anchor = self._cluster_anchor(turn, ranger)
            if cluster_anchor is not None and planner.toward(
                ranger, cluster_anchor, "vanguard_dance_flee"
            ):
                decisions.append(
                    f"ranger:{_short_id(ranger.id)} vanguard_dance FLEE_AMBUSH "
                    f"flee_to={cluster_anchor}"
                )
                self.memory.decision_totals["ranger:vanguard_dance"] += 1
            else:
                ranger.wait()
                decisions.append(
                    f"ranger:{_short_id(ranger.id)} vanguard_dance FLEE_AMBUSH wait"
                )
            # 其他游侠预瞄追兵必经路 → 由常规打分制 legal 分支处理(先锋追击时上一步
            # 朝我方友军→approach_forward=True→FORWARD 高分选追兵推进格)。本 tick 其他
            # 游侠对该先锋开枪由 legal 分支 _enemy_is_flee_ambush_pursuer 计 ambush_trade。
            # 保留 FLEE_AMBUSH 配对到下一 tick(供其他游侠本 tick 检测);逃跑游侠回 hp2
            # 或先锋离视野时清掉(见 _find_vanguard_dance_target 启动前的清理 + observe 清理)。
            self.memory.vanguard_dance_phase[pair_key]["phase"] = "FLEE_AMBUSH"
            self.memory.vanguard_dance_phase[pair_key]["flee_tick"] = turn.tick
            return True

        # 舞步配对过期清理:逃跑游侠已回满血(hp2)或先锋已不在 kiting 范围 → 清配对。
        if phase == "FLEE_AMBUSH" and ranger.hp >= 2:
            self.memory.vanguard_dance_phase.pop(pair_key, None)
            return False

        return False

    def _vanguard_dance_gap_cell(
        self, ranger: Ranger, enemy: UnitView
    ) -> Position | None:
        """游侠与先锋之间的中间格(先锋贴脸必经格)。仅当二者共线(横/竖/对角)时存在。"""
        rx, ry = ranger.position
        ex, ey = enemy.position
        dx, dy = ex - rx, ey - ry
        # 共线且距离=2:中间格 = ranger + 单位方向。
        if abs(dx) == 2 and dy == 0:
            return (rx + (1 if dx > 0 else -1), ry)
        if abs(dy) == 2 and dx == 0:
            return (rx, ry + (1 if dy > 0 else -1))
        if abs(dx) == 2 and abs(dy) == 2:
            return (rx + (1 if dx > 0 else -1), ry + (1 if dy > 0 else -1))
        return None

    def _vanguard_dance_back_direction(
        self, ranger: Ranger, enemy: UnitView
    ) -> Direction | None:
        """先锋→游侠向量的反方向(游侠后退方向,远离先锋)。仅 cardinal。"""
        rx, ry = ranger.position
        ex, ey = enemy.position
        dx, dy = rx - ex, ry - ey  # 远离先锋的方向
        if abs(dx) >= abs(dy) and dx != 0:
            return Direction.RIGHT if dx > 0 else Direction.LEFT
        if abs(dy) > abs(dx) and dy != 0:
            return Direction.DOWN if dy > 0 else Direction.UP
        return None

    def _can_ranger_move_to(
        self, turn: Turn, planner: MovementPlanner, ranger: Ranger, direction: Direction
    ) -> bool:
        """游侠能否朝该方向走一格(非障碍、非敌、容量未满)。用于 dance 后退判定。"""
        dest = _destination(ranger.position, direction)
        if dest in planner.obstacles or dest in planner.enemy_cells:
            return False
        return planner.final_occupancy(dest) < 2

    def _cluster_anchor(self, turn: Turn, ranger: Ranger) -> Position | None:
        """最近友游侠(非自己)或 Core,作为受伤游侠逃跑目的地。"""
        others = [r for r in turn.rangers if r.id != ranger.id]
        if others:
            nearest = min(others, key=lambda r: (_distance(r.position, ranger.position), r.id.bytes))
            return nearest.position
        if turn.core is not None:
            return turn.core.position
        return None

    def _enemy_is_flee_ambush_pursuer(
        self, turn: Turn, enemy: UnitView | CoreView, shooter: Ranger
    ) -> bool:
        """该敌是否正作为某 FLEE_AMBUSH 舞步的追兵(且 shooter 不是逃跑者本人)。

        FLEE_AMBUSH 时逃跑游侠已清配对并逃向集群,其他游侠对追兵(该先锋)开枪
        即集群设伏。检查 vanguard_dance_phase 里 phase==FLEE_AMBUSH 的配对,
        其 enemy 段 == 该敌 id,且配对 ranger 段 != shooter.id。
        """
        if not isinstance(enemy, UnitView):
            return False
        enemy_id = str(enemy.id)
        shooter_id = str(shooter.id)
        for pair_key, state in self.memory.vanguard_dance_phase.items():
            if state.get("phase") != "FLEE_AMBUSH":
                continue
            parts = pair_key.split("|", 1)
            if len(parts) != 2:
                continue
            pair_ranger, pair_enemy = parts
            if pair_enemy == enemy_id and pair_ranger != shooter_id:
                return True
        return False

    def _ranger_assault_fire_cell(
        self,
        turn: Turn,
        target: UnitView,
        planner: MovementPlanner,
        rangers: Iterable[Ranger],
    ) -> Position | None:
        """为主动突击选公共射击格：三格 45°优先，其次三格直线。

        旧的 45°逻辑只在很窄的 ``StrategicStandoff`` 条件下挑一个支援
        游侠，而且 fallback 仍然把所有相持游侠排除了，导致算法看起来
        ""总是不生效""。这里把射击格计算从僵局检测中解耦：只要敌情在
        游侠视野内，就先找可达的三格斜线格；没有再退到三格横/竖线，
        最后才使用任意合法射击格。
        """
        target_cell = target.position
        all_cells = self._firing_cells(target_cell, planner.obstacles)
        if not all_cells:
            return None
        blind_cells = self._enemy_blind_firing_cells(
            turn, target_cell, planner.obstacles
        )
        ranger_positions = tuple(ranger.position for ranger in rangers)

        def rank(cell: Position) -> tuple[int, int, int, int, Position]:
            dx = abs(cell[0] - target_cell[0])
            dy = abs(cell[1] - target_cell[1])
            diagonal_far = dx == 3 and dy == 3
            straight_far = (dx == 3 and dy == 0) or (dx == 0 and dy == 3)
            if diagonal_far and cell in blind_cells:
                category = 0
            elif diagonal_far:
                category = 1
            elif straight_far and cell in blind_cells:
                category = 2
            elif straight_far:
                category = 3
            elif cell in blind_cells:
                category = 4
            else:
                category = 5
            # 选择离小队整体最近的公共点，避免四人从两侧拉成长蛇。
            nearest = min(
                (_distance(position, cell) for position in ranger_positions),
                default=0,
            )
            total = sum(_distance(position, cell) for position in ranger_positions)
            return (category, nearest, total, -len(
                [
                    direction
                    for direction in DIRECTION_ORDER
                    if _destination(cell, direction) not in planner.obstacles
                ]
            ), cell)

        return min(all_cells, key=rank)

    def _ranger_assault_plan(
        self,
        turn: Turn,
        planner: MovementPlanner,
        acted_units: set[UUID],
        vacancy_by_id: dict[UUID, Vacancy],
    ) -> RangerAssaultPlan | None:
        """发现敌情后固定抽取四名游侠，2 预瞄 + 2 主动突击。"""
        eligible = [
            ranger
            for ranger in turn.rangers
            if ranger.hp == 2
            and ranger.id not in acted_units
            and ranger.id not in vacancy_by_id
        ]
        if len(eligible) < 4:
            return None
        targets = [
            enemy
            for enemy in turn.visible_enemies
            if isinstance(enemy, UnitView)
            and enemy.unit_type in {UnitType.RANGER, UnitType.VANGUARD, UnitType.WORKER}
            and any(
                _unit_can_see_position(ranger, enemy.position, planner.obstacles)
                for ranger in eligible
            )
        ]
        if not targets:
            return None
        target = min(
            targets,
            key=lambda enemy: (
                _enemy_role_priority(enemy),
                min(_distance(ranger.position, enemy.position) for ranger in eligible),
                _effective_hp(enemy),
                enemy.id.bytes,
            ),
        )
        ordered = sorted(
            eligible,
            key=lambda ranger: (
                _distance(ranger.position, target.position),
                ranger.id.bytes,
            ),
        )[:4]
        fire_cell = self._ranger_assault_fire_cell(turn, target, planner, ordered)
        if fire_cell is None:
            return None
        return RangerAssaultPlan(
            target=target,
            fire_cell=fire_cell,
            preaim_ids=tuple(ranger.id for ranger in ordered[:2]),
            assault_ids=tuple(ranger.id for ranger in ordered[2:4]),
        )

    def _execute_ranger_preaim(
        self,
        turn: Turn,
        planner: MovementPlanner,
        ranger: Ranger,
        target: UnitView,
        ledger: ShotLedger,
        decisions: list[str],
    ) -> bool:
        """给编组中的两名卡位游侠锁定目标当前/下一格，禁止落回巡逻。"""
        scored = self._score_aim_cells(turn, target, planner, context="standoff_escape")
        legal = [
            (cell, score)
            for cell, score in scored
            if _is_legal_ranger_shot(ranger.position, cell, planner.obstacles)
            and ledger.can_assign(target, predicted=cell != target.position)
        ]
        if legal:
            cell, _ = max(legal, key=lambda item: (item[1], _cell_sort_key(item[0])))
            ranger.shoot(target, expected_cell=cell)
            ledger.assign(ranger, target, cell)
            self._mark_ranger_shot(
                target,
                cell,
                blind=ranger.position
                in self._enemy_blind_firing_cells(
                    turn, cell, planner.obstacles
                ),
            )
            decisions.append(
                f"ranger:{_short_id(ranger.id)} assault_preaim "
                f"target={_short_id(target.id)} cell={cell}"
            )
            self.memory.decision_totals["ranger:assault_preaim"] += 1
            self.memory.decision_totals["ranger:shot"] += 1
            return True
        # 当前站位没有射击线时，预瞄游侠也要主动换到目标候选格的
        # 射击位；只有连一个合法射击位都找不到才原地占位。
        aim_cells = [cell for cell, _ in scored]
        reposition_cells: list[Position] = []
        for aim_cell in aim_cells:
            reposition_cells.extend(
                candidate
                for candidate in self._firing_cells(aim_cell, planner.obstacles)
                if candidate not in planner.enemy_cells
                and (
                    candidate == ranger.position
                    or planner.final_occupancy(candidate) < 2
                )
            )
        if reposition_cells:
            goal = min(
                reposition_cells,
                key=lambda cell: (
                    _distance(ranger.position, cell),
                    _distance(cell, target.position),
                    _cell_sort_key(cell),
                ),
            )
            if ranger.position != goal and planner.toward(
                ranger, goal, "ranger_assault_preaim_reposition"
            ):
                decisions.append(
                    f"ranger:{_short_id(ranger.id)} assault_preaim_advance "
                    f"target={_short_id(target.id)} goal={goal}"
                )
                self.memory.decision_totals["ranger:assault_preaim_advance"] += 1
                return True
        ranger.wait()
        decisions.append(
            f"ranger:{_short_id(ranger.id)} assault_preaim_wait "
            f"target={_short_id(target.id)}"
        )
        self.memory.decision_totals["ranger:assault_preaim_wait"] += 1
        return True

    def _execute_ranger_assault(
        self,
        turn: Turn,
        planner: MovementPlanner,
        ranger: Ranger,
        assault: RangerAssaultPlan,
        ledger: ShotLedger,
        decisions: list[str],
    ) -> bool:
        """执行突击位：当前能打就打，否则两名游侠向同一格叠加推进。"""
        target = assault.target
        if _is_legal_ranger_shot(ranger.position, target.position, planner.obstacles):
            if ledger.can_assign(target, predicted=False):
                ranger.shoot(target, expected_cell=target.position)
                ledger.assign(ranger, target, target.position)
                self._mark_ranger_shot(target, target.position, blind=(
                    ranger.position
                    in self._enemy_blind_firing_cells(
                        turn, target.position, planner.obstacles
                    )
                ))
                decisions.append(
                    f"ranger:{_short_id(ranger.id)} assault_shot "
                    f"target={_short_id(target.id)} cell={target.position}"
                )
                self.memory.decision_totals["ranger:assault_shot"] += 1
                self.memory.decision_totals["ranger:shot"] += 1
                return True
        if ranger.position != assault.fire_cell and planner.toward(
            ranger, assault.fire_cell, "ranger_assault_45"
        ):
            decisions.append(
                f"ranger:{_short_id(ranger.id)} assault_advance "
                f"target={_short_id(target.id)} fire_cell={assault.fire_cell}"
            )
            self.memory.decision_totals["ranger:assault_advance"] += 1
            return True
        ranger.wait()
        decisions.append(
            f"ranger:{_short_id(ranger.id)} assault_wait "
            f"target={_short_id(target.id)} fire_cell={assault.fire_cell}"
        )
        self.memory.decision_totals["ranger:assault_wait"] += 1
        return True

    def _ranger_direct_assault_target(
        self,
        turn: Turn,
        ranger: Ranger,
        planner: MovementPlanner,
    ) -> UnitView | None:
        """少于四名游侠时，也不能对先锋/工人永远原地预瞄。"""
        targets = [
            enemy
            for enemy in turn.visible_enemies
            if isinstance(enemy, UnitView)
            and enemy.unit_type in {UnitType.VANGUARD, UnitType.WORKER}
            and _unit_can_see_position(ranger, enemy.position, planner.obstacles)
        ]
        if not targets:
            return None
        return min(
            targets,
            key=lambda enemy: (
                0 if enemy.unit_type is UnitType.VANGUARD else 1,
                _distance(ranger.position, enemy.position),
                enemy.id.bytes,
            ),
        )

    def _execute_ranger_direct_assault(
        self,
        turn: Turn,
        planner: MovementPlanner,
        ranger: Ranger,
        target: UnitView,
        decisions: list[str],
    ) -> bool:
        fire_cell = self._ranger_assault_fire_cell(
            turn, target, planner, (ranger,)
        )
        if fire_cell is None:
            return False
        if ranger.position != fire_cell and planner.toward(
            ranger, fire_cell, "ranger_direct_assault"
        ):
            decisions.append(
                f"ranger:{_short_id(ranger.id)} direct_assault_advance "
                f"target={_short_id(target.id)} fire_cell={fire_cell}"
            )
            self.memory.decision_totals["ranger:direct_assault_advance"] += 1
            return True
        return False

    def _choose_rangers_lightning(
        self, turn: Turn, planner: MovementPlanner, acted_units: set[UUID], decisions: list[str],
    ) -> None:
        if turn.core is None:
            return
        plan = self._lightning_plan or self._lightning_prepare_plan(turn, planner, decisions)
        vacancy_by_id = {vacancy.ranger_id: vacancy for vacancy in plan.vacancies}
        relief_by_id = {relief.ranger_id: relief for relief in plan.reliefs}
        ledger = ShotLedger()
        contacts_by_id = {contact.enemy.id: contact for contact in plan.threats}
        # 泛化战略相持:游侠互瞄死锁 / 被围残血先锋 / 被堵逃命工人。
        # 检出即指派一名满血游侠走到对角最远位换血(用户:主动触发、低限制、常用)。
        # 相持时双方都在预瞄对方下一步→原位恰好无人瞄,支援游侠直接射敌当前格。
        strategic_standoff = self._detect_strategic_standoff(turn)
        standoff = strategic_standoff.enemy if strategic_standoff is not None else None
        standoff_relay_cell = (
            self._standoff_relay_cell(turn, standoff, planner) if standoff is not None else None
        )
        # 相持触发计数:检出战略相持且能算出换血位即计一次。
        if strategic_standoff is not None and standoff_relay_cell is not None:
            self.memory.decision_totals["ranger:standoff_engaged"] += 1
        # 45°支援游侠挑选(放宽 _distance>3 限制,优先调第三方破僵局)。
        # T4 核心告急时跳过(Core 存活优先):exclude 含已投入 T4 contact 的游侠。
        t4_imminent = any(
            c.tier == "T4" and c.core_eta <= 1 for c in plan.threats
        )
        assault_plan = (
            None
            if t4_imminent
            else self._ranger_assault_plan(
                turn, planner, set(acted_units), vacancy_by_id
            )
        )
        if assault_plan is not None:
            decisions.append(
                f"ranger:assault_squad target={_short_id(assault_plan.target.id)} "
                f"preaim={','.join(_short_id(uid) for uid in assault_plan.preaim_ids)} "
                f"assault={','.join(_short_id(uid) for uid in assault_plan.assault_ids)} "
                f"fire_cell={assault_plan.fire_cell}"
            )
            self.memory.decision_totals["ranger:assault_squad"] += 1
        standoff_relay_ranger_id: UUID | None = None
        standoff_support_ranger_id: UUID | None = None
        if strategic_standoff is not None and standoff_relay_cell is not None and not t4_imminent:
            # 互瞄环 exclude_ids = 距敌 ≤4 的游侠(已在相持里的)。
            exclude_ids = {
                r.id for r in turn.rangers
                if _distance(r.position, standoff.position) <= 4
            }
            support_ranger = self._pick_diagonal_support_ranger(
                turn, strategic_standoff, planner,
                acted_units=acted_units, vacancy_by_id=vacancy_by_id,
                relay_cell=standoff_relay_cell, exclude_ids=exclude_ids,
            )
            if support_ranger is not None:
                standoff_support_ranger_id = support_ranger.id
                # 推进指派沿用旧字段名(复用既有 standoff_relay_advance 移动分支)。
                standoff_relay_ranger_id = support_ranger.id
        # FLEE_AMBUSH 预标记:遍历 REAIM_GAP_HP2 配对,若该游侠本 tick 掉到 hp1
        # (先锋 hp2 时没逃反攻了我),把配对标 FLEE_AMBUSH,供其他游侠 legal 分支计数
        # ambush_trade。MEDIVAC 分支会接管该 hp1 游侠回 Core(即"逃向集群"的撤退部分)。
        for pair_key, state in list(self.memory.vanguard_dance_phase.items()):
            if state.get("phase") != "REAIM_GAP_HP2":
                continue
            pair_ranger_str = pair_key.split("|", 1)[0]
            pair_ranger = next(
                (r for r in turn.rangers if str(r.id) == pair_ranger_str), None
            )
            if pair_ranger is not None and pair_ranger.hp <= 1:
                state["phase"] = "FLEE_AMBUSH"
                state["flee_tick"] = turn.tick
        # 本 tick 同 sector eta_support/funnel_cover 已认领的射击格(防扎堆贴墙)。
        eta_claimed_cells: set[Position] = set()
        for ranger in sorted(turn.rangers, key=_uuid_key):
            if ranger.id in acted_units:
                continue
            if self._comet_step_unit(
                turn, planner, ranger, self._comet_target_tick,
                acted_units, decisions,
                ledger=ledger, contacts_by_id=contacts_by_id,
            ):
                continue
            candidates = self._ranger_shot_candidates(turn, ranger, planner)
            # MEDIVAC is default at 1 HP.  LAST_STAND is intentionally narrow:
            # a legal shot against T4 that prevents an immediate Core attack.
            medivac = vacancy_by_id.get(ranger.id)
            legal = []
            for enemy, cell in candidates:
                predicted = cell != enemy.position
                contact = contacts_by_id.get(enemy.id)
                if ledger.can_assign(enemy, predicted=predicted):
                    legal.append((enemy, cell, contact, predicted))
            if medivac is not None:
                last_stand = [item for item in legal if item[2] is not None and item[2].tier == "T4" and item[2].core_eta <= 1]
                if last_stand:
                    enemy, cell, _, _ = min(last_stand, key=lambda item: (_effective_hp(item[0]), item[0].id.bytes, item[1]))
                    # 站在自己认领的盲区换血位上开枪 = 盲区射击（对峙换血机制产出）。
                    on_blind_cell = (
                        standoff_relay_cell is not None
                        and ranger.position == standoff_relay_cell
                    )
                    ranger.shoot(enemy, expected_cell=cell); ledger.assign(ranger, enemy, cell); self._mark_ranger_shot(enemy, cell, blind=on_blind_cell)
                    decisions.append(f"ranger:{_short_id(ranger.id)} LAST_STAND shot target={_short_id(enemy.id)}")
                    self.memory.decision_totals["ranger:last_stand"] += 1
                    self.memory.decision_totals["ranger:shot"] += 1
                else:
                    at_home = ranger.position == turn.core.position
                    if not at_home and not planner.toward(ranger, turn.core.position, "ranger_medivac"):
                        ranger.wait()
                    decisions.append(f"ranger:{_short_id(ranger.id)} MEDIVAC home={medivac.t_home} gap={medivac.t_medical_gap}")
                    self.memory.decision_totals["ranger:medivac"] += 1
                    # At the Core the unit must remain unreserved so the later
                    # healing phase can spend a resource on it this Tick.
                    if at_home:
                        continue
                acted_units.add(ranger.id)
                continue
            # 发现敌情后固定四人分工：两名游侠只负责预瞄卡位，另外两名
            # 共用一个主动射击格，优先三格 45°斜线，找不到再走三格直线。
            # 这两个分支必须在通用巡逻/预瞄之前介入，否则游侠会一直
            # 停在原地等待敌人犯错。
            if assault_plan is not None and ranger.id in assault_plan.assault_ids:
                self._execute_ranger_assault(
                    turn, planner, ranger, assault_plan, ledger, decisions
                )
                acted_units.add(ranger.id)
                continue
            if assault_plan is not None and ranger.id in assault_plan.preaim_ids:
                self._execute_ranger_preaim(
                    turn, planner, ranger, assault_plan.target, ledger, decisions
                )
                acted_units.add(ranger.id)
                continue
            # Mode B:游侠单杀先锋舞步(在 legal 射击之前介入,T4 核心告急时跳过)。
            if not t4_imminent:
                dance_enemy = self._find_vanguard_dance_target(turn, ranger)
                if dance_enemy is not None:
                    if self._resolve_vanguard_dance(
                        turn, planner, ranger, dance_enemy, ledger, decisions
                    ):
                        acted_units.add(ranger.id)
                        continue
            # Mode A:45°支援游侠射敌原位(相持时原位无人瞄)。
            # 置于通用 legal 之前→支援游侠瞄原位胜出;其他游侠 fall through 走打分制。
            # 仅当本格已对敌原位有合法射击线(横/竖/45°对角,距离 1-3,无障碍)才开火。
            # ——旧条件 at_relay(reach 可达 8)允许"近 relay 格但未进合法射击线"就开火,
            # 导致超射程(距离>3 或非对齐)的 SHOT_MISSED(看得见命中、实际无效)。
            # 原设计"走不动但本格已有合法射击线"的语义完全由 has_line 覆盖;未到合法线时
            # 落入下方 standoff_relay_advance 推进分支继续走过去。
            if (
                standoff_support_ranger_id == ranger.id
                and strategic_standoff is not None
                and standoff_relay_cell is not None
                and standoff is not None
            ):
                support_enemy = standoff
                contact = contacts_by_id.get(support_enemy.id)
                # 若该敌是 T4 即将打 Core,让 LAST_STAND 接管(下个 medivac 循环会处理)。
                if contact is None or not (contact.tier == "T4" and contact.core_eta <= 1):
                    has_line = _is_legal_ranger_shot(
                        ranger.position, support_enemy.position, planner.obstacles
                    )
                    if has_line and ledger.can_assign(support_enemy, predicted=False):
                        cell = support_enemy.position
                        on_blind_cell = ranger.position in self._enemy_blind_firing_cells(
                            turn, cell, planner.obstacles
                        )
                        ranger.shoot(support_enemy, expected_cell=cell)
                        ledger.assign(ranger, support_enemy, cell)
                        self._mark_ranger_shot(support_enemy, cell, blind=on_blind_cell)
                        decisions.append(
                            f"ranger:{_short_id(ranger.id)} diagonal_support "
                            f"target={_short_id(support_enemy.id)} "
                            f"kind={strategic_standoff.kind} cell={cell}"
                        )
                        self.memory.decision_totals["ranger:diagonal_support"] += 1
                        self.memory.decision_totals["ranger:shot"] += 1
                        # 开火即清零停滞计数(支援生效,不是死追)。
                        self.memory.standoff_support_stall.pop(str(ranger.id), None)
                        acted_units.add(ranger.id)
                        continue
            if legal:
                def priority(item: tuple[UnitView | CoreView, Position, ThreatContact | None, bool]) -> tuple:
                    enemy, cell, contact, predicted = item
                    return (0 if contact and contact.tier in {"T4", "T3"} else 1,
                            contact.core_eta if contact else 99, _enemy_role_priority(enemy),
                            _effective_hp(enemy), 1 if predicted else 0, enemy.id.bytes, cell)
                enemy, cell, _, _ = min(legal, key=priority)
                on_blind_cell = (
                    standoff_relay_cell is not None
                    and ranger.position == standoff_relay_cell
                )
                ranger.shoot(enemy, expected_cell=cell); ledger.assign(ranger, enemy, cell); self._mark_ranger_shot(enemy, cell, blind=on_blind_cell)
                decisions.append(f"ranger:{_short_id(ranger.id)} ShotLedger shoot target={_short_id(enemy.id)} assigned={ledger.assigned_damage[enemy.id]}")
                self.memory.decision_totals["ranger:shot"] += 1
                # FLEE_AMBUSH 设伏:若该敌正是某舞步处于 FLEE_AMBUSH 阶段的追兵,
                # 该游侠(非逃跑者本人)的开枪即集群预瞄追兵必经路。
                if self._enemy_is_flee_ambush_pursuer(turn, enemy, ranger):
                    self.memory.decision_totals["ranger:ambush_trade"] += 1
                acted_units.add(ranger.id)
                continue

            # 没有四人编组时，先锋/工人仍要触发主动走位；只把“预瞄”
            # 留给敌方游侠之间的远程对峙。否则单个游侠会在轴线脱靶后
            # 回到轨道巡逻，永远不去寻找新的覆盖位置。
            direct_target = self._ranger_direct_assault_target(
                turn, ranger, planner
            )
            if direct_target is not None and self._execute_ranger_direct_assault(
                turn, planner, ranger, direct_target, decisions
            ):
                acted_units.add(ranger.id)
                continue

            # 对峙僵局换血位推进：优先于巡逻/支援移动，但不抢占已有合法射击
            # （上一分支已 continue）与 MEDIVAC（更早分支已 continue）。
            # 死追保护:relay_cell 随敌每 tick 移动,支援游侠可能永远到不了合法射击线
            # → 反复推进却不开火 → 被从巡逻/作战剥离(贴墙不动不攻击)。连续未开火达阈值
            # 即冷却该游侠若干 tick,回落常规巡逻/作战分支,不再死追移动靶。
            if (
                standoff_relay_ranger_id == ranger.id
                and standoff_relay_cell is not None
                and ranger.position != standoff_relay_cell
            ):
                stall = self.memory.standoff_support_stall.get(str(ranger.id), 0) + 1
                if stall >= STANDOFF_SUPPORT_MAX_STALL_TICKS:
                    self.memory.standoff_support_cooldown[str(ranger.id)] = (
                        turn.tick + STANDOFF_SUPPORT_COOLDOWN_TICKS
                    )
                    self.memory.standoff_support_stall.pop(str(ranger.id), None)
                    self.memory.decision_totals["ranger:standoff_relay_abandon"] += 1
                    decisions.append(
                        f"ranger:{_short_id(ranger.id)} standoff_relay_abandon "
                        f"stall={stall}"
                    )
                    # 不 continue:回落下方常规巡逻/作战分支,本 tick 照常动起来。
                else:
                    self.memory.standoff_support_stall[str(ranger.id)] = stall
                    if self._lightning_step_toward(
                        turn, planner, ranger, standoff_relay_cell, "standoff_relay_advance"
                    ):
                        decisions.append(
                            f"ranger:{_short_id(ranger.id)} standoff_relay_advance "
                            f"target={_short_id(standoff.id) if standoff else '?'} "
                            f"cell={standoff_relay_cell}"
                        )
                        self.memory.decision_totals["ranger:standoff_relay"] += 1
                        acted_units.add(ranger.id)
                        continue
                    # 走不动(被卡)→ 放弃本 tick 指派,回落常规分支。
            relief = relief_by_id.get(ranger.id)
            if relief is not None:
                goal = relief.fire_position
                reason = "ranger_relief"
            else:
                same_sector = [contact for contact in plan.threats if contact.sector == self._lightning_sector(turn.core.position, ranger.position) and contact.tier != "T0"]
                if same_sector:
                    contact = min(same_sector, key=lambda c: (c.core_eta, c.square_radius))
                    goal = self._lightning_safe_firing_position(turn, planner, ranger, contact.enemy.position, contact.sector, plan.funnel.gate_cell, claimed=eta_claimed_cells)
                    reason = "ranger_eta_support"
                elif plan.funnel.gate_cell is not None:
                    goal = self._lightning_safe_firing_position(turn, planner, ranger, plan.funnel.gate_cell, self._lightning_sector(turn.core.position, plan.funnel.gate_cell), plan.funnel.gate_cell)
                    reason = "ranger_funnel_cover"
                else:
                    # None 表示游侠已到轨道点（_lightning_orbit_waypoint 内 target==position）。
                    # 不再用 `or ranger.position` 吞成"原地"——那会让下方 position!=goal 为假,
                    # 既不 move 也不 wait,游侠本 tick 完全零指令(607c149 引入的回归)。
                    goal = self._lightning_orbit_waypoint(turn, ranger, UnitType.RANGER)
                    reason = "mid_orbit_patrol"
            # 记下本 tick eta_support/funnel_cover 已认领的射击格,后续同 sector 游侠散开。
            if reason in ("ranger_eta_support", "ranger_funnel_cover"):
                eta_claimed_cells.add(goal)
            # goal 为 None(已到轨道点)或等于本格(已在射击位)→ 显式 wait 占位,
            # 防被同环友军抢格;与先锋/工人的 `orbit is not None and not step: wait` 一致。
            if goal is None or ranger.position == goal:
                ranger.wait()
            elif not self._lightning_step_toward(turn, planner, ranger, goal, reason):
                ranger.wait()
            decisions.append(f"ranger:{_short_id(ranger.id)} {reason} goal={goal}")
            self.memory.decision_totals[f"{reason}"] += 1
            acted_units.add(ranger.id)
        if ledger.intents:
            decisions.append(f"ShotLedger intents={len(ledger.intents)} targets={len(ledger.assigned_damage)}")


    def _core_patrol_slots(
        self,
        turn: Turn,
        planner: MovementPlanner,
        patrol_rangers: list[Ranger],
    ) -> dict[UUID, Position]:
        if turn.core is None or not patrol_rangers:
            return {}
        offsets = _terrain_guard_offsets(
            turn.core.position,
            planner.obstacles,
            (
                (0, -CORE_PATROL_RADIUS),
                (CORE_PATROL_RADIUS, 0),
                (0, CORE_PATROL_RADIUS),
                (-CORE_PATROL_RADIUS, 0),
            ),
        )
        open_count, open_axis, concentrated_count, _ = _core_attack_surface_profile(
            turn.core.position,
            planner.obstacles,
        )
        terrain_backed = (
            open_axis is not None
            and open_count <= MIGRATION_SITE_MAX_OPEN_RANGED_CELLS
            and concentrated_count * MIGRATION_SITE_MIN_OPEN_HALF_RATIO_DENOMINATOR
            >= open_count * MIGRATION_SITE_MIN_OPEN_HALF_RATIO_NUMERATOR
        )
        open_offset_count = len(offsets)
        if terrain_backed and open_axis is not None:
            axis_x, axis_y = open_axis
            open_offset_count = sum(
                dx * axis_x + dy * axis_y >= 0 for dx, dy in offsets
            )
        phase = (
            turn.tick // CORE_PATROL_ROTATION_TICKS
        ) % max(1, open_offset_count)
        reserved: set[Position] = set()
        slots: dict[UUID, Position] = {}
        for index, ranger in enumerate(patrol_rangers):
            if terrain_backed:
                preferred = (phase + index) % max(1, open_offset_count)
                candidate_indexes = tuple(
                    (preferred + delta) % len(offsets)
                    for delta in range(len(offsets))
                )
            else:
                preferred = (phase + index * 2) % len(offsets)
                candidate_indexes = (
                    preferred,
                    (preferred + 1) % len(offsets),
                    (preferred - 1) % len(offsets),
                    (preferred + 2) % len(offsets),
                )
            for candidate_index in candidate_indexes:
                dx, dy = offsets[candidate_index]
                position = turn.core.position[0] + dx, turn.core.position[1] + dy
                if (
                    position in reserved
                    or position in planner.obstacles
                    or position in planner.enemy_cells
                    or position in turn.resource_cells
                    or (
                        position != ranger.position
                        and planner.final_occupancy(position) >= 2
                    )
                ):
                    continue
                slots[ranger.id] = position
                reserved.add(position)
                break
        return slots

    def _firing_cells(self, target: Position, obstacles: set[Position]) -> set[Position]:
        cells: set[Position] = set()
        for dx, dy in RANGER_LINE_DELTAS:
            cell = target
            for _ in range(3):
                cell = (cell[0] + dx, cell[1] + dy)
                if cell in obstacles:
                    break
                if _line_clear(cell, target, obstacles):
                    cells.add(cell)
        return cells

    def _enemy_blind_firing_cells(
        self,
        turn: Turn,
        target: Position,
        obstacles: set[Position],
    ) -> set[Position]:
        """对 target 的合法射击格中，敌方游侠/先锋看不见的子集（盲区火力位）。

        复用 _firing_cells 的射线枚举（1-3 距离、横/竖/45°对角、无障碍），
        再过滤掉敌方观察者视野内的格。站在盲区格的游侠不会被敌方预瞄，
        可能无伤命中。
        """
        watchers = _enemy_watchers(turn)
        if not watchers:
            return set()
        return {
            cell
            for cell in self._firing_cells(target, obstacles)
            if not _enemy_can_see_cell(watchers, cell, obstacles)
        }

    def _find_core_shelter(
        self,
        turn: Turn,
        planner: MovementPlanner,
    ) -> tuple[Position, Position] | None:
        """Find or retain a visible empty cell with exactly one cardinal entrance."""
        core = turn.core
        if core is None:
            return None

        obstacles = planner.obstacles
        current_entrance = _shelter_entrance(core.position, obstacles)
        if current_entrance is not None:
            self.memory.core_shelter_target = core.position
            self.memory.core_shelter_entrance = current_entrance
            return core.position, current_entrance

        remembered_target = self.memory.core_shelter_target
        remembered_entrance = self.memory.core_shelter_entrance
        if (
            remembered_target is not None
            and remembered_entrance is not None
            and _distance(core.position, remembered_target)
            <= CORE_SHELTER_MEMORY_MAX_DISTANCE
            and remembered_target not in obstacles
            and _shelter_entrance(remembered_target, obstacles) == remembered_entrance
        ):
            return remembered_target, remembered_entrance

        self.memory.clear_core_shelter_memory()
        candidates: list[tuple[tuple[int, int, int, int, Position], Position, Position]] = []
        radius = AGGRESS_CORE_SHELTER_SEARCH_RADIUS
        for dx in range(-radius, radius + 1):
            for dy in range(-radius, radius + 1):
                if abs(dx) + abs(dy) > radius:
                    continue
                candidate = (core.position[0] + dx, core.position[1] + dy)
                if candidate in obstacles or candidate in planner.enemy_cells:
                    continue
                if candidate in turn.resource_cells:
                    continue
                if planner.final_occupancy(candidate) > (1 if candidate == core.position else 0):
                    continue
                entrance = _shelter_entrance(candidate, obstacles)
                if entrance is None or entrance in planner.enemy_cells:
                    continue
                if entrance in turn.resource_cells:
                    continue
                if planner.final_occupancy(entrance) >= 2 and entrance != core.position:
                    continue
                if not (
                    _currently_visible(turn, candidate, obstacles)
                    or self.memory.visited.get(candidate, 0) > 0
                ):
                    continue
                if not (
                    _currently_visible(turn, entrance, obstacles)
                    or self.memory.visited.get(entrance, 0) > 0
                ):
                    continue

                blocked = set(obstacles) | set(planner.enemy_cells)
                blocked.update(
                    position
                    for position, until in self.memory.temporary_blocks.items()
                    if until > turn.tick
                )
                blocked.update(
                    position
                    for position in planner.occupancy
                    if position not in {core.position, entrance}
                    and planner.final_occupancy(position) >= 2
                )
                if core.position != entrance and not _find_path(
                    core.position,
                    entrance,
                    blocked=blocked,
                    threat=planner.threat,
                    visited=self.memory.visited,
                ):
                    continue
                score = (
                    _distance(core.position, entrance),
                    planner.threat.get(entrance, 0),
                    self.memory.visited.get(candidate, 0),
                    _distance(core.position, candidate),
                    candidate,
                )
                candidates.append((score, candidate, entrance))

        if not candidates:
            return None
        _, target, entrance = min(candidates)
        self.memory.core_shelter_target = target
        self.memory.core_shelter_entrance = entrance
        return target, entrance

    def _unit_capped(self, unit_type: UnitType, turn: Turn) -> bool:
        """该兵种是否达到网页控制台设定的独立上限（0/缺省 = 无上限）。

        控制文件 unit_caps 用小写 key（{"worker":..,"vanguard":..,"ranger":..}），
        unit_type.value 是大写，这里统一转小写查。
        """
        cap = self.memory.unit_caps.get(unit_type.value.lower(), 0)
        if cap <= 0:
            return False
        count = sum(1 for unit in turn.units if unit.unit_type == unit_type)
        return count >= cap

    def _lightning_ratio_spawn(
        self,
        turn: Turn,
        died: dict[str, str],
    ) -> UnitType | None:
        """统一选兵：阈值驱动主动补兵 + 比例趋近增长（从第 1 个单位起一视同仁）。

        died: 本 tick 阵亡单位 {uid: unit_type 名}（保留签名，新逻辑不直接依赖——
              阵亡只通过拉低当前数量间接触发阈值/比例趋近补兵）。

        规则：
        1. spawn_ratio 三者全 0 → 返回 None（停止造兵，囤资源）。
        2. 某兵种 spawn_ratio=0 → 该兵种永不主动生产（停造）。
        3. 主动补兵：当前数量 < replenish_threshold 的兵种，按 replenish_priority
           顺序取优先级最高的补一个。多个低于阈值时按优先级逐一补。
        4. 无兵种低于阈值 → 按 spawn_ratio 比例趋近：选归一化计数
           (count/share) 最低的非零比例兵种补，平局按补兵优先级。
        5. 所选兵种达 unit_caps 封顶 → 按优先级找下一个未封顶的非零比例兵种；
           全封顶返回 None。
        """
        rk = len(turn.rangers)
        wk = len(turn.workers)
        vg = len(turn.vanguards)
        counts = {
            UnitType.RANGER: rk,
            UnitType.WORKER: wk,
            UnitType.VANGUARD: vg,
        }
        ratio = self.memory.spawn_ratio
        shares = {
            UnitType.RANGER: ratio.get("ranger", 1),
            UnitType.WORKER: ratio.get("worker", 3),
            UnitType.VANGUARD: ratio.get("vanguard", 1),
        }
        threshold = self.memory.replenish_threshold
        thresholds = {
            UnitType.RANGER: threshold.get("ranger", 0),
            UnitType.WORKER: threshold.get("worker", 0),
            UnitType.VANGUARD: threshold.get("vanguard", 0),
        }
        _key_to_type = {
            "ranger": UnitType.RANGER,
            "vanguard": UnitType.VANGUARD,
            "worker": UnitType.WORKER,
        }
        priority_types = [
            _key_to_type[key]
            for key in self.memory.replenish_priority
            if key in _key_to_type
        ]
        for key, ut in _key_to_type.items():
            if ut not in priority_types:
                priority_types.append(ut)

        # 规则①：比例全 0 → 停止造兵囤资源。
        if all(shares[u] == 0 for u in counts):
            return None

        def _respect_cap(choice: UnitType) -> UnitType | None:
            # 所选兵种封顶 → 按补兵优先级找替代（仍须是非零比例且未封顶）。
            if not self._unit_capped(choice, turn):
                return choice
            for u in priority_types:
                if (
                    u is not choice
                    and shares[u] > 0
                    and not self._unit_capped(u, turn)
                ):
                    return u
            return None

        # 规则③：主动补兵——低于阈值的兵种按优先级补。
        under_threshold = [
            u for u in priority_types
            if thresholds[u] > 0 and counts[u] < thresholds[u]
        ]
        if under_threshold:
            return _respect_cap(under_threshold[0])

        # 规则④：按比例趋近——归一化计数最低的非零比例兵种，平局按优先级。
        def _norm(u: UnitType) -> float:
            return counts[u] / shares[u] if shares[u] > 0 else float("inf")

        ranked = sorted(
            (u for u in counts if shares[u] > 0),
            key=lambda u: (_norm(u), priority_types.index(u)),
        )
        for u in ranked:
            choice = _respect_cap(u)
            if choice is not None:
                return choice
        return None

    def _select_spawn_with_source(
        self,
        turn: Turn,
        projected_resources: int,
    ) -> tuple[UnitType | None, bool]:
        """Return the Unit this Core would produce if its cell had capacity.

        返回 (target_type, from_queue)。from_queue=True 表示本次选择来自
        网页控制台预定队列（成功后由 _consume_build_queue 消费）。
        """
        core = turn.core
        if core is None:
            return None, False

        current_population = len(turn.units)
        worker_cost = unit_cost(UnitType.WORKER, current_population)
        vanguard_cost = unit_cost(UnitType.VANGUARD, current_population)
        ranger_cost = unit_cost(UnitType.RANGER, current_population)

        # 硬顶 ABSOLUTE_MAX_POPULATION:不再产兵。
        if current_population >= ABSOLUTE_MAX_POPULATION:
            return None, False

        # 阵亡补同种:读 observe 里算好的本 tick 阵亡 dict(每 tick 一次,避免
        # _select_spawn 被多次调用时重复消费/清空 last_alive)。
        died = dict(self.memory.lightning_recent_deaths)

        near_threat = any(
            _distance(core.position, enemy.position) <= 5
            for enemy in turn.visible_enemies
        )
        owned_ids = {unit.id for unit in turn.units} | {core.id}
        owns_beacon = (
            turn.beacon.status is BeaconStatus.CARRIED
            and turn.beacon.carrier_id in owned_ids
        )
        shield_cap = 10 if owns_beacon else 5
        # 战时保留：近敌或补盾期留 2 资源应急（原有行为）。
        combat_reserve = 2 if near_threat or core.shield < shield_cap else 0
        # 资源存底（网页控制台可配）：和平期存 wartime_reserve(默认 150) 给战时
        # 医疗/补兵。规则②产能兜底：capacity-reserve 不足以造一个游侠时（人口 ≤
        # ~40 的爬坡期）无视存底继续造兵抬上限——否则"存满 → 造不出兵 → 人口不涨
        # → 上限不涨 → 永久卡死"。每 tick 现场算，不硬编码人口阈值。
        # 规则①存底：capacity-reserve 装得下一个游侠时，只有超出 reserve 的部分
        # 可用于造兵。战时（near_threat/补盾）存底让位给原有威胁分支。
        reserve_floor = self.memory.wartime_reserve
        hold_floor = (
            reserve_floor
            if (
                not near_threat
                and core.shield >= shield_cap
                and turn.resource_capacity - reserve_floor >= ranger_cost
            )
            else 0
        )
        budget = projected_resources - combat_reserve - hold_floor

        # 闪电模式产兵（统一规则，无固定前置阶梯；仅开局引导：前 3 个造工人）：
        #   优先级 = 预定队列 → 开局引导(前3个工人) → 补兵阈值/补兵优先级规则
        #   → 各兵种独立上限 → 正常比例趋近。
        #   网页控制台：build_queue 非空时优先于一切；unit_caps 逐兵种独立
        #   封顶（某兵到上限只停它自己，不影响其他兵种）。
        queue = list(self.memory.build_queue)
        if queue:
            # 预定队列优先：从队首扫描，取第一个未达上限的兵种。
            from_queue = True
            target_type = None
            for item in queue:
                candidate = _unit_type_from_name(item)
                if candidate is not None and not self._unit_capped(candidate, turn):
                    target_type = candidate
                    break
            if target_type is None:
                # 队列里所有兵种都已封顶 → 本 tick 不造（队列保留）。
                return None, False
        else:
            from_queue = False
            # 开局引导：当前工人 < 3 且已造引导工人数 < 3 且工人未封顶
            # → 固定造工人，保证起手经济。当前工人数检查避免在已发展期
            # （夹具/重连）误触发；引导计数保证累计造满 3 个后永久关闭。
            if (
                self.memory.bootstrap_workers_built < 3
                and len(turn.workers) < 3
                and not self._unit_capped(UnitType.WORKER, turn)
            ):
                target_type = UnitType.WORKER
            else:
                # 引导结束：阈值补兵优先，否则按 spawn_ratio 比例趋近；
                # 各兵种独立 unit_caps 在 _lightning_ratio_spawn 内部封顶。
                target_type = self._lightning_ratio_spawn(turn, died)
        if target_type is None:
            return None, from_queue
        cost = (
            worker_cost
            if target_type is UnitType.WORKER
            else vanguard_cost
            if target_type is UnitType.VANGUARD
            else ranger_cost
        )
        if budget >= cost:
            return target_type, from_queue
        return None, from_queue  # 攒钱下个 tick（保留队列优先级意图）

    def _select_spawn(
        self,
        turn: Turn,
        projected_resources: int,
    ) -> UnitType | None:
        return self._select_spawn_with_source(turn, projected_resources)[0]

    def _persist_core_hold_after_arrival(self) -> None:
        """Core 到达用户目标后自动开启驻扎：把控制文件 core_target=null、
        core_hold=true 原子写回，让前端轮询时同步显示。memory 里的值已由
        调用方设好，这里只同步落盘 + 刷新 mtime（防下 tick load_control 误判
        为外部改动）。只改这两个字段，保留控制文件其余内容。
        """
        if self.control_path is None:
            return
        try:
            data = json.loads(self.control_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return
            data["core_target"] = None
            data["core_hold"] = True
            temporary = self.control_path.with_suffix(
                self.control_path.suffix + ".tmp"
            )
            temporary.write_text(
                json.dumps(data, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            temporary.replace(self.control_path)
            self.control_mtime = self.control_path.stat().st_mtime_ns
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            pass

    def _consume_build_queue(self, spawned_type: str) -> None:
        """真实 spawn 成功后消费预定队列（内存 + 控制文件双消费，带竞态守卫）。

        只移除 spawned_type 的首次出现；控制文件重写基于文件当前的 build_queue，
        只改该字段、不覆盖其他外部改动。
        """
        queue = list(self.memory.build_queue)
        if spawned_type in queue:
            queue.remove(spawned_type)
            self.memory.build_queue = queue
        try:
            data = json.loads(self.control_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return
            raw_queue = data.get("build_queue")
            if not isinstance(raw_queue, list):
                return
            file_queue = [
                str(item) for item in raw_queue if isinstance(item, str)
            ]
            if spawned_type not in file_queue:
                return
            file_queue.remove(spawned_type)
            data["build_queue"] = file_queue
            temporary = self.control_path.with_suffix(
                self.control_path.suffix + ".tmp"
            )
            temporary.write_text(
                json.dumps(data, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            temporary.replace(self.control_path)
            self.control_mtime = self.control_path.stat().st_mtime_ns
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            pass

    def _choose_core(
        self,
        turn: Turn,
        planner: MovementPlanner,
        core_acted: bool,
        incoming_deposit: int,
        decisions: list[str],
    ) -> None:
        core = turn.core
        if core is None or core_acted:
            return
        if core.view.state is CoreState.MOVING:
            if core.view.move_direction is not None:
                self.memory.core_heading = core.view.move_direction
            return
        owned_ids = {unit.id for unit in turn.units} | {core.id}
        owns_beacon = (
            turn.beacon.status is BeaconStatus.CARRIED
            and turn.beacon.carrier_id in owned_ids
        )
        shield_cap = 10 if owns_beacon else 5
        projected_resources = turn.resources + min(incoming_deposit, turn.resource_space)
        plan = self._lightning_plan
        near_threat = bool(plan and any(contact.tier in {"T3", "T4"} for contact in plan.threats))
        if (
            projected_resources >= 1
            and core.hp < 5
            and callable(getattr(core, "heal", None))
        ):
            core.heal()
            decisions.append(
                f"core heal hp={core.hp}/5 resources={turn.resources} "
                f"projected={projected_resources}"
            )
            self.memory.decision_totals["core:heal"] += 1
            return

        if (
            projected_resources >= 1
            and core.shield < shield_cap
            and (near_threat or core.shield <= 2)
        ):
            core.repair_shield()
            decisions.append(
                f"core repair_shield shield={core.shield}/{shield_cap} threat={near_threat}"
            )
            self.memory.decision_totals["core:repair"] += 1
            return

        can_spawn = (
            planner.final_occupancy(core.position) < 2
            and True
        )
        spawn = None
        from_queue = False
        if can_spawn:
            # An unfilled physical funnel is more urgent than the nominal 3:1
            # roster.  Conversely, an unrelieved medical vacancy asks for a Ranger.
            # 网页控制台 unit_caps 对两个应急分支同样生效：封顶兵种不再被创建。
            # spawn_ratio=0 同样生效(用户:某兵种比例设 0 = 停造该兵种,应急分支不得
            # 绕过——否则医疗空缺分支会持续造游侠,即使玩家把游侠比例设 0)。
            def _ratio_allows(unit_type: UnitType) -> bool:
                return self.memory.spawn_ratio.get(
                    unit_type.value.lower(), 0
                ) > 0
            if (
                plan is not None
                and plan.anchor is CoreAnchorState.COMBAT_ANCHOR
                and plan.funnel.shortfall > 0
                and _ratio_allows(UnitType.WORKER)
                and not self._unit_capped(UnitType.WORKER, turn)
                and projected_resources >= unit_cost(UnitType.WORKER, turn.state.population)
            ):
                spawn = UnitType.WORKER
                self.memory.decision_totals["lightning:spawn_funnel_worker"] += 1
            elif (
                plan is not None
                and plan.vacancies
                and len(plan.reliefs) < len(plan.vacancies)
                and _ratio_allows(UnitType.RANGER)
                and not self._unit_capped(UnitType.RANGER, turn)
                and projected_resources >= unit_cost(UnitType.RANGER, turn.state.population)
            ):
                spawn = UnitType.RANGER
                self.memory.decision_totals["lightning:spawn_medical_ranger"] += 1
            else:
                spawn, from_queue = self._select_spawn_with_source(
                    turn, projected_resources
                )

        if spawn is not None:
            core.spawn(spawn)
            replacement = self.memory.replacement_queue[spawn.value] > 0
            decisions.append(
                f"core spawn {spawn.value} resources={turn.resources} "
                f"projected={projected_resources} replacement={replacement} "
                f"queue={from_queue}"
            )
            self.memory.decision_totals[f"core:spawn:{spawn.value}"] += 1
            if from_queue:
                self._consume_build_queue(spawn.value)
            # 开局引导计数：累计造满 3 个工人后关闭固定工人引导。
            if (
                spawn is UnitType.WORKER
                and self.memory.bootstrap_workers_built < 3
            ):
                self.memory.bootstrap_workers_built += 1
            # Core 每 tick 只有一个动作:造兵(不移动 Core)后即结束,不进入移动/迁移逻辑。
            # 驻扎时造兵照常,但绝不因 fall-through 到巡逻/迁移而抵消驻扎意图。
            return
        elif projected_resources >= 1 and core.shield < shield_cap:
            core.repair_shield()
            decisions.append(f"core repair_shield reason=spare_resources shield={core.shield}")
            self.memory.decision_totals["core:repair"] += 1
            # 修盾同造兵:不移动 Core,本 tick 到此结束。
            return
        # === 网页控制台：驻扎 / 目标坐标覆盖 anchor_hold 与巡逻 ===
        # anchor_hold 是为“巡逻漂移/战斗医疗卡点”设计的自主停驻；但当玩家明确
        # 下达 core_hold 或 core_target 时，该指令必须优先，否则在线对战只要有
        # 任何 T3/T4 威胁或 funnel 空缺（几乎所有实战），core_target 转移都会被
        # anchor_hold 吞掉、Core 永远不挪窝。因此先判玩家指令，再回落到自主逻辑。
        #
        # core_hold 必须在 heal/repair/spawn 之后、所有移动/迁移逻辑之前拦截。
        # heal/repair/spawn 不移动 Core,驻扎时照常执行;但一旦本 tick 要进入巡逻/
        # anchor_hold/迁移(会朝入侵相反方向逃跑的"本能"),驻扎必须压制它,停在原地。
        # 旧代码把 core_hold 放在 else 分支里→只在"没 heal/repair/spawn"时生效,造兵
        # 的 tick 会 fall through 到迁移逻辑→ Core 面对入侵时逃跑。
        if self.memory.core_hold:
            decisions.append("core hold=true")
            self.memory.decision_totals["core:hold"] += 1
            return
        # 御驾亲征：开启后 core_target 每 tick 动态指向信标位置（复用彗星 beacon
        # 模式的目标解析思路）。优先级低于驻扎，高于恒星巡逻。下方 core_target 分支
        # 会按当前 core_transfer_mode 走 star/march/fortify 转移；到达后不停驻（信标
        # 会动），持续跟随。退避/追击偏置在 _choose_core_migration 内叠加生效。
        # 与用户手设 core_target 互斥：御驾亲征开启时无视持久化的 core_target。
        if self.memory.core_pursue_beacon:
            beacon_pos = turn.beacon.position
            self.memory.core_target = beacon_pos
            self.memory.core_target_kind = "beacon"
            core_target = beacon_pos
        else:
            core_target = self.memory.core_target
        if core_target is not None:
            if self.memory.core_target_kind == "beacon":
                # 御驾亲征：信标动态移动，到达不停驻，每 tick 重新指向最新 beacon
                # 位置（上面已写入 core_target）。无论是否到达附近都持续走转移推进，
                # 退避/追击偏置在 _choose_core_migration 内叠加生效。
                decisions.append(
                    f"lightning core_transfer target={core_target} "
                    f"mode={self.memory.core_transfer_mode} kind=beacon"
                )
                self.memory.decision_totals["lightning:core_transfer"] += 1
                fast_transfer = self.memory.core_transfer_mode in {"march", "fortify"}
                self._choose_core_migration(
                    turn,
                    planner,
                    incoming_deposit,
                    decisions,
                    migration_target=core_target,
                    noncombat_enemies_safe=True,
                    ignore_beacon_progress=True,
                    user_directed_transfer=fast_transfer,
                    enemy_bias=self._core_enemy_bias(turn),
                )
                return
            if self._core_target_reached(turn):
                if self.memory.core_target_kind == "orbit":
                    # 轨道迁移到达：清 target，直接开始恒星巡逻（不停驻）。
                    # 不走下方 anchor_hold/no_orbit 早返回——立即进入巡逻。
                    self.memory.core_target = None
                    self.memory.core_target_kind = "user"
                    decisions.append(
                        f"core orbit_arrived target={core_target} → patrol"
                    )
                    self.memory.decision_totals["core:orbit_arrived"] += 1
                    core_target = None  # 本块剩余迁移代码不再执行
                    # 落入下方 r>0 巡逻分支（不 return）。
                else:
                    # 到达用户目标 → 自动开启驻扎模式 + 清空 target：
                    # 急行军/坚壁清野的工人限制只作用于迁移途中，到达后立刻
                    # 恢复正常采集提交。写回控制文件让前端同步显示 hold=true、target=null。
                    self.memory.core_target = None
                    self.memory.core_target_kind = "user"
                    self.memory.core_hold = True
                    self.memory.lightning_patrol_waypoint = None
                    self._persist_core_hold_after_arrival()
                    decisions.append(
                        f"core target_arrived target={core_target} → hold=true"
                    )
                    self.memory.decision_totals["core:target_arrived"] += 1
                    return
            else:
                decisions.append(
                    f"lightning core_transfer target={core_target} "
                    f"mode={self.memory.core_transfer_mode}"
                )
                self.memory.decision_totals["lightning:core_transfer"] += 1
                # 急行军/坚壁清野：Core 马不停蹄，不被近圈 cargo 工人 logistics_hold
                # 卡住（march 工人本就空载，fortify 工人需到目标后才提交）。
                # 恒星模式按定义就是“Core 停下等工人提交”，故仍允许 logistics_hold。
                fast_transfer = self.memory.core_transfer_mode in {"march", "fortify"}
                self._choose_core_migration(
                    turn,
                    planner,
                    incoming_deposit,
                    decisions,
                    migration_target=core_target,
                    noncombat_enemies_safe=True,
                    ignore_beacon_progress=True,
                    user_directed_transfer=fast_transfer,
                    enemy_bias=self._core_enemy_bias(turn),
                )
                return
        if plan is not None and plan.anchor is not CoreAnchorState.MOBILE_EVADE:
            decisions.append(f"core anchor_hold state={plan.anchor.value} funnel_shortfall={plan.funnel.shortfall}")
            self.memory.decision_totals[f"core:anchor_hold:{plan.anchor.value}"] += 1
            return
        # 恒星轨道半径 r 未设置（=0）：Core 不巡逻，停在原地等设置。
        if self._lightning_patrol_radius() <= 0:
            decisions.append("core orbit_radius=0 (no patrol)")
            self.memory.decision_totals["core:no_orbit"] += 1
            return
        # In the absence of medical/combat service, patrol keeps avoiding the
        # visible combat direction through _choose_core_migration scoring.
        waypoint = self._lightning_patrol_waypoint(turn)
        decisions.append(
            f"lightning patrol waypoint={waypoint} "
            f"phase={self.memory.lightning_patrol_phase}"
        )
        self.memory.decision_totals["lightning:patrol"] += 1
        self._choose_core_migration(
            turn,
            planner,
            incoming_deposit,
            decisions,
            beacon_target=waypoint,
            noncombat_enemies_safe=True,
            ignore_beacon_progress=True,
            enemy_bias=self._core_enemy_bias(turn),
        )

    def _choose_core_migration(
        self,
        turn: Turn,
        planner: MovementPlanner,
        incoming_deposit: int,
        decisions: list[str],
        beacon_target: Position | None = None,
        shelter_target: Position | None = None,
        migration_target: Position | None = None,
        noncombat_enemies_safe: bool = False,
        ignore_beacon_progress: bool = False,
        user_directed_transfer: bool = False,
        enemy_bias: str = "none",
    ) -> None:
        """Core 单步迁移评分选向。

        enemy_bias: 退避三舍/趁胜追击偏置，仅在敌方可见时生效。
          - "evade"：让候选方向远离敌方（destination 比当前更远离最近敌方 → 加成）。
          - "chase"：让候选方向靠近敌方（destination 比当前更靠近 → 加成）。
          - "none"：不偏置（仅原有 combat_proximity_penalty 生效）。
          退避与追击互斥，调用方保证（同时开时传 "evade"）。敌方视野消失时调用方
          应传 "none"，Core 恢复沿恒星轨道/转移目标推进。
        """
        core = turn.core
        if core is None or core.view.state is not CoreState.NORMAL:
            return
        cargo_workers = [worker for worker in turn.workers if worker.cargo]
        if incoming_deposit > 0:
            return
        service_workers = [
            worker
            for worker in cargo_workers
            if _distance(core.position, worker.position)
            <= CORE_MIGRATION_CARGO_SERVICE_RADIUS
            and not (
                len(
                    recent := self.memory.recent_positions.get(str(worker.id), [])
                )
                >= STUCK_TICKS
                and len(set(recent)) <= SPIN_POSITION_BUDGET
            )
        ]
        if service_workers and not user_directed_transfer:
            # 玩家下达 core_target 急行军/坚壁清野转移时，cargo 工人只是按模式
            # 带货随行（march 工人本就空载、fortify 工人需到目标后才提交）。
            # 若仍让 Core 就地等待交付，fortify 模式下近圈带货工人会永久卡住 Core，
            # 导致“目标设了却不动”——与 anchor_hold 并列的转移失效根因。
            nearest_cargo = min(
                _distance(core.position, worker.position)
                for worker in service_workers
            )
            decisions.append(
                "core logistics_hold "
                f"nearest_cargo={nearest_cargo} "
                f"radius={CORE_MIGRATION_CARGO_SERVICE_RADIUS}"
            )
            self.memory.decision_totals["core:logistics_hold"] += 1
            return
        if core.hp < 5 or core.shield < 3:
            return
        if noncombat_enemies_safe:
            # 闪电模式：工人视野提前发现敌方战斗单位时，Core 应绕开，而不是冲过去
            # 再停。只有被战斗单位包围（每个方向都更接近某个战斗单位）才停下补盾。
            combat_enemies = [
                enemy
                for enemy in turn.visible_enemies
                if isinstance(enemy, UnitView)
                and enemy.unit_type in {UnitType.VANGUARD, UnitType.RANGER}
            ]
        else:
            combat_enemies = []
        owns_beacon = _owns_beacon(turn)

        if migration_target is not None:
            targets = [migration_target]
            reason = "migration_target"
        elif shelter_target is not None:
            targets = [shelter_target]
            reason = "shelter"
        elif beacon_target is not None:
            targets = [beacon_target]
            reason = "beacon_distance_ctrl"
        elif cargo_workers:
            # 只向被挡在远处的 cargo 工人靠拢（近的能自己交付）
            targets = [
                worker.position
                for worker in cargo_workers
                if _distance(core.position, worker.position) > 5
            ]
            if not targets:
                targets = [worker.position for worker in cargo_workers]
            reason = "rendezvous_cargo"
        else:
            targets = [
                goal.position
                for worker in turn.workers
                if not worker.cargo
                and (goal := self.memory.worker_goals.get(str(worker.id))) is not None
                and goal.kind != "resource_recovery"
            ]
            if targets:
                reason = "follow_worker_goals"
            else:
                targets = [worker.position for worker in turn.workers]
                reason = "follow_workers"
        if not targets:
            if owns_beacon:
                return
            targets = [turn.beacon.position]
            reason = "advance_beacon"

        candidates: list[tuple[float, int, Direction, Position]] = []
        current_beacon_distance = _distance(core.position, turn.beacon.position)
        for direction in DIRECTION_ORDER:
            destination = _destination(core.position, direction)
            if (
                destination in planner.obstacles
                or destination in planner.enemy_cells
                or planner.final_occupancy(destination) >= 2
            ):
                continue
            # One Core step costs four Ticks. Normalize for fleet size and resist
            # undoing the last step while faster Workers are still catching up.
            if self.memory.core_heading is None:
                heading_penalty = 0.0
            elif direction == self.memory.core_heading:
                heading_penalty = 0.0
            elif direction == OPPOSITE_DIRECTION[self.memory.core_heading]:
                heading_penalty = (
                    8.0
                    if turn.tick - self.memory.last_core_move_tick
                    <= CORE_DIRECTION_COMMIT_TICKS
                    else 1.0
                )
            else:
                heading_penalty = 1.0
            target_distance = sum(
                _distance(destination, target) for target in targets
            ) / len(targets)
            beacon_progress = 0
            if not owns_beacon:
                beacon_progress = (
                    current_beacon_distance
                    - _distance(destination, turn.beacon.position)
                )
                if (
                    beacon_progress < 0
                    and beacon_target is None
                    and shelter_target is None
                    and migration_target is None
                ):
                    continue
            # 闪电模式绕行：工人视野提前发现敌方战斗单位时，Core 应避开该方向，
            # 而非冲过去再停。给"走过去更靠近某个战斗单位"的方向加大惩罚。
            # 工人/Core 无攻击力不计；只有先锋(sweep)/游侠(shoot)真能伤 Core。
            combat_proximity_penalty = 0.0
            if combat_enemies:
                nearest_after = min(
                    _distance(destination, enemy.position)
                    for enemy in combat_enemies
                )
                nearest_before = min(
                    _distance(core.position, enemy.position)
                    for enemy in combat_enemies
                )
                if nearest_after < nearest_before:
                    # 越靠近越重罚；走进游侠射程(3)或先锋相邻(1)致命，几乎一票否决。
                    if nearest_after <= 3:
                        combat_proximity_penalty = 50.0
                    else:
                        combat_proximity_penalty = 12.0
            # 退避三舍 / 趁胜追击偏置：基于"走这一步后离最近敌方距离"的变化。
            # 用全部可见敌方（含 Core/Worker，不只战斗单位）——退避/追击针对的是
            # "敌方出现在视野中"这件事本身。delta>0 表示这一步远离敌方，<0 表示靠近。
            # 退避：奖励远离（delta>0 减分），靠近不额外罚（combat_proximity_penalty
            #   已罚致命靠近，这里只加"远离加分"避免双重罚把 Core 推进死角）。
            # 追击：奖励靠近（delta<0 减分），即 -|delta|*w。
            # 权重 3.0：一格距离变化 ±3 分，与 heading_penalty(1~8)、visited(≤8) 同量级，
            # 足以改变方向选择但不会压过障碍/威胁(×20)与致命靠近(50)。
            enemy_bias_score = 0.0
            if enemy_bias in ("evade", "chase") and turn.visible_enemies:
                nearest_enemy_after = min(
                    _distance(destination, enemy.position)
                    for enemy in turn.visible_enemies
                )
                nearest_enemy_before = min(
                    _distance(core.position, enemy.position)
                    for enemy in turn.visible_enemies
                )
                delta = nearest_enemy_after - nearest_enemy_before
                if enemy_bias == "evade":
                    # 远离加分（delta>0 时减分，使该方向更优）。
                    enemy_bias_score = -max(0.0, delta) * 3.0
                else:  # chase
                    # 靠近加分（delta<0 时减分）。
                    enemy_bias_score = min(0.0, delta) * 3.0
            score = (
                target_distance
                + planner.threat.get(destination, 0) * 20
                + heading_penalty
                + combat_proximity_penalty
                + enemy_bias_score
                - _chunk_quota(_chunk_of(destination)) * 0.1
                # visited 是惩罚(与 A*/flee 一致),不是"跟足迹"加成:Core 一步站
                # 4 tick,离开格已累积 +4 visited,若是加成会把 Core 拉回刚离开的
                # 格形成 ping-pong 鬼打墙。惩罚权重与掉头罚同量级,压死角横跳。
                + min(8.0, self.memory.visited.get(destination, 0) * 0.3)
                - (
                    beacon_progress * BEACON_PROGRESS_WEIGHT
                    if shelter_target is None and not ignore_beacon_progress
                    else 0
                )
            )
            candidates.append(
                (score, DIRECTION_RANK[direction], direction, destination)
            )
        if not candidates:
            return
        # 闪电模式被战斗单位包围兜底：≥2 个战斗单位从不同方向夹击，且不存在
        # 任何"逃离方向"（比所有战斗单位都远）时，才停下修盾。单个战斗单位
        # 总能绕开（评分惩罚已让 Core 选远离方向），不算包围。
        if noncombat_enemies_safe and len(combat_enemies) >= 2:
            has_escape = any(
                all(
                    _distance(dest, enemy.position)
                    > _distance(core.position, enemy.position)
                    for enemy in combat_enemies
                )
                for _, _, _, dest in candidates
            )
            if not has_escape:
                decisions.append("core patrol_hold reason=combat_surrounded")
                self.memory.decision_totals["lightning:patrol_hold_combat"] += 1
                return

        _, _, direction, destination = min(candidates)
        core.start_move(direction)
        self.memory.core_heading = direction
        self.memory.last_core_move_tick = turn.tick
        self.memory.decision_totals[f"core:move:{reason}"] += 1
        nearest_cargo = (
            min(_distance(core.position, worker.position) for worker in cargo_workers)
            if cargo_workers
            else None
        )
        decisions.append(
            f"core start_move {direction.value} destination={destination} "
            f"reason={reason} nearest_cargo={nearest_cargo} "
            f"beacon={turn.beacon.position} "
            f"beacon_distance={_distance(destination, turn.beacon.position)}"
        )
