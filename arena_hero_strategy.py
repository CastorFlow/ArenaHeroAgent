from __future__ import annotations

import heapq
import json
import math
import os
import time
from collections import Counter
from dataclasses import dataclass, field
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

MODE_LIGHTNING = "lightning"
MODE_VALUES = {
    MODE_LIGHTNING,
}
# 闪电模式：在远离高强度战区的贫瘠坐标方环（挖空方形甜甜圈，500 ≤
# max(|x|,|y|) ≤ 700）内泊 Core，靠击杀刚复活、无护卫的敌方 Core（每杀 +5
# 资源）加速发育。战斗单位各自独立路线扫场，不组队。
# lightning_ring = (inner_radius, outer_radius)，默认方环；可经控制文件覆盖。
LIGHTNING_DEFAULT_RING = (500, 700)
# 巡逻半径偏外环（用户：内圈火力猛，Core 不深入）。0.75 → 半径 ≈650。
LIGHTNING_PATROL_RADIUS_FRACTION = 0.75
# 巡逻点沿半径 pr 的方形周界四角轮转；到位（进入 CORE_BEACON_HYSTERESIS 死区）后换下一角。
LIGHTNING_PATROL_COMPASS = (
    (1, 0), (1, 1), (0, 1), (-1, 1),
    (-1, 0), (-1, -1), (0, -1), (1, -1),
)
# 猎手 claim 的敌方 Core 半径内出现敌方战斗单位即视为"附近有守卫"，作为障碍绕开。
LIGHTNING_HUNT_GUARD_RADIUS = 8
# 守卫贴脸到此距离内（真能 sweep/shoot 到进攻方）才释放 claim 撤退；更远的绕开即可。
LIGHTNING_HUNT_GUARD_CLOSE_RADIUS = 3
# 目标 Core 周围 GUARD_RADIUS 内近期 sighting 达此数即视为重兵把守，放弃猎杀。
# 补住"守卫在雾里看不见 → 误判无护卫 → 凑过去卡死"的漏洞。
LIGHTNING_CROWD_THRESHOLD = 2
LIGHTNING_CROWD_SIGHTING_MAX_AGE = 40
# 猎手扇区探索的步长（限制不出方环）。
LIGHTNING_SECTOR_STEP = 6
# 闪电模式常驻兵力上限。绕银河轨道体系要更多角色铺子轨道,提到 20。
# 注意:20+ 触发官方涨价档(+30%/5人口),后期长期造游侠会多吃涨价——这是
# "非必要不进攻、发育为主、要更多角色扩轨道"路线接受的取舍。
LIGHTNING_MAX_POPULATION = 20
# 绝对人口上限：资源容量管理允许额外工人（20-100 区间纯工人，用于消耗资源+扩容量）。
# 战斗配置保持 20 人不变，额外工人不影响游侠/先锋比例。
ABSOLUTE_MAX_POPULATION = 100
# 游侠同心周界 lane 间距：相邻游侠的方环半径错开 6 格（游侠视野 5，6>直径 10/2
# 不重叠），径向铺开多条同心周界，N 游侠沿各自周界同向绕圈共同覆盖 Core 轨道。
LIGHTNING_SCOUT_LANE_GAP = 6
# 先锋 V 字纵深出探的深度（一来回 ~64 tick，Core 1格/4tick 前进 ~16 格，
# 下一轮覆盖全新带；内陆最远 32 格，危险时 ~16 tick 回防）。
LIGHTNING_VEE_DEPTH = 32
LIGHTNING_VEE_REACH_TOLERANCE = 3
LIGHTNING_VEE_HOME_TOLERANCE = 5
# 同一敌方 Core 最多多少单位同时集火（防全员扑一个导致 Core 失防、或扑远目标
# 时旁边敌方 Core 没人盯）。
LIGHTNING_FOCUS_MAX_ATTACKERS = 3
# 猎杀距离上限：只追击距离己方 Core 此距离内的敌方 Core，防全员被吸走太远。
# 设为 outer_ring + 200，在方环外侧附近给一点追击空间，但不会追到几百格外。
LIGHTNING_HUNT_MAX_DISTANCE = 900
# 敌方 Core sighting 过期时间（ticks）：超过此时间未再见到的 sighting 视为陈旧，
# 自动清理以防止单位永久 hunt 已不存在的目标。300 ticks ≈ 75秒。
LIGHTNING_SIGHTING_MAX_AGE = 300

# === 绕银河多层轨道体系 ===
# Core 轨道（恒星绕银心）绕原点 (0,0) 转 pr≈650 方环，慢；其余四类轨道围绕它：
#   开路轨道（恒星维度，绕原点外更大同心方环）、近/中/远行星轨道（绕 Core 转圈）。
# 开路轨道半径比 Core 轨道 pr 更外的同心跳数；起步用一个游侠视野直径量级，
# 待运行后视覆盖深度再调。
# 行星轨道层序(内→外):先锋(近行星,半径 LIGHTNING_NEAR_ORBIT_RADIUS=5，
#   贴 Core 视野边缘) → 游侠(中行星) → 工人(远行星,仅闲时上轨)。见
#   _lightning_orbit_lane_radius。游侠中轨护 Core 中层、工人远轨点亮外围迷雾。
LIGHTNING_BREAKTHROUGH_RING_OFFSET = 12
# 固定 4 个开路游侠，最先排满；产出第 5 个游侠起进中行星轨道（绕 Core 中层）。
LIGHTNING_BREAKTHROUGH_SLOT_COUNT = 4
# 开路轨道安全阈值：Core 距原点超过此值时禁用 breakthrough（防止游侠孤军深入被击杀）。
# 当 Core 远离原点时，开路的"提前点亮原点资源"意义不大，所有游侠改围 Core 护卫。
LIGHTNING_BREAKTHROUGH_MAX_CORE_DISTANCE = 400
# 局部威胁感知半径：游侠/先锋执行轨道巡逻时，检测周围此半径内的敌方战斗单位。
# 发现威胁时执行局部避战（暂停巡逻，撤向 Core 或绕开），防止孤军深入被围杀。
LIGHTNING_LOCAL_THREAT_RADIUS = 8
# 固定产兵阶梯（用户指定，严格按 pop 槽位填，攒钱优先不 fallthrough）：
#   pop0→先锋, 1→工人, 2→游侠, 3→工人, 4→游侠, 5→工人, 6→游侠, 7→工人,
#   8+→游侠(直到 LIGHTNING_MAX_POPULATION)。只造 1 先锋(先锋弱,工人当肉盾)。
# 容量 max(10,pop*5) 自然走通：pop0 cap10≥先锋10; pop1 cap10≥工人5; pop2 cap15≥游侠12。
LIGHTNING_BUILD_ORDER: tuple[UnitType, ...] = (
    UnitType.VANGUARD,  # slot 0
    UnitType.WORKER,    # slot 1
    UnitType.RANGER,    # slot 2
    UnitType.WORKER,    # slot 3
    UnitType.RANGER,    # slot 4
    UnitType.WORKER,    # slot 5
    UnitType.RANGER,    # slot 6
    UnitType.WORKER,    # slot 7
)
# 各 role 行星子轨道径向间距起步 = 该角色视野半径（相邻两层视野恰好相切，
# 覆盖连续不重叠）。后续按产能/盲区情况调。
LIGHTNING_ORBIT_LANE_GAP_RADIUS: dict[UnitType, int] = {
    UnitType.VANGUARD: 4,
    UnitType.WORKER: 3,
    UnitType.RANGER: 5,
}
# 行星轨道叠格死区（到角多久推下一角）沿用 CORE_BEACON_HYSTERESIS=8。
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
# 巡逻角障碍密度跳角：目标角周围 5x5（25 格）内已知障碍 > 此值（40%）时，
# 且单位距角尚远（> 死区*2），视为"角在乱石堆里"，提前推进下一角绕行。
LIGHTNING_CORNER_OBSTACLE_LIMIT = 10
# 回防分级——按 visible 敌方战斗单位到我 Core 最近距离 d_min 分档触发反应强度：
LIGHTNING_DEFENSE_RING_NEAR = 6   # 近环/Core 贴身：全体含工人回防线卡位肉盾
                                  # （沿用 _core_emergency 6 格阈值；仅用于回防分档，
                                  #  不是先锋轨道半径——先锋轨道半径用下方
                                  #  LIGHTNING_NEAR_ORBIT_RADIUS，两者解耦）。
# 先锋（近行星）轨道半径：贴 Core 视野边缘转。Core 视野=5 是 Manhattan 半径；
# 轨道是 max-norm 方环，半径 5 的方环对角格 max-norm=5、恰在 Core 视野对角边缘，
# 正方向格在视野内、对角格贴视野外沿——"贴着 core 视野边缘转、增加保护力"。
LIGHTNING_NEAR_ORBIT_RADIUS = 5
# MID/FAR 起步值待覆盖铺开后按实际近/中/远总展宽调（见 STRATEGY.md §12 验证段）。
LIGHTNING_DEFENSE_RING_MID = 20   # 中环：全体游侠回防（工人继续经济或就地卡位）。
LIGHTNING_DEFENSE_RING_FAR = 40   # 外环：仅那个方位附近游侠游击警告，不全撤。
DEVELOP_TARGET_WORKERS = 12
DEVELOP_TARGET_VANGUARDS = 3
DEVELOP_TARGET_RANGERS = 3
# A distant Beacon needs a head start: waiting for the complete 3+3 home
# reserve plus the full expedition can postpone first contact for thousands of
# Ticks when local resource income is sparse.  Keep Develop-mode workers on the
# economy, but release one Vanguard/Ranger scout pair once a safe 2+1 home
# screen remains.
DEVELOP_EARLY_BEACON_MIN_DISTANCE = CHUNK_SIZE * 2
DEVELOP_EARLY_BEACON_MIN_VANGUARDS = 3
DEVELOP_EARLY_BEACON_MIN_RANGERS = 2
# Once the fixed 3+3 home reserve is restored, form this separate force before
# switching to beacon mode.  The reserve itself never leaves the Core.
DEVELOP_BEACON_EXPEDITION_VANGUARDS = 1
DEVELOP_BEACON_EXPEDITION_RANGERS = 2
DEVELOP_SEARCH_INITIAL_RADIUS = 10
DEVELOP_SEARCH_STEP = 8
# 侵略模式：4 工人维持经济，游侠占战斗编制多数。
# 召回时先锋贴身 core 的分散位（core 4 邻 + 对角，避免全挤 core 位置）
VANGUARD_RECALL_OFFSETS = (
    (1, 0), (-1, 0), (0, 1), (0, -1),
    (1, 1), (-1, -1), (1, -1), (-1, 1),
)
# 召回时游侠回 core 周围的分散位（2 格环，避免路径冲突）
RANGER_RECALL_OFFSETS = (
    (0, -2), (2, 0), (0, 2), (-2, 0),
    (2, -2), (-2, -2), (2, 2), (-2, 2),
)
# core 是否允许自动迁移（false = 固定不动）
CORE_MIGRATION_ENABLED = False
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
DEFAULT_RAID_VANGUARDS = 1
DEFAULT_RAID_RANGERS = 2
# 守家编制是所有外派任务的硬底线：1 先锋 + 1 游侠（原 AGGRESS_DEFENDER 编制）。
RAID_HOME_RESERVE_VANGUARDS = 1
RAID_HOME_RESERVE_RANGERS = 1
RAID_HOME_RESERVE_COMBAT = (
    RAID_HOME_RESERVE_VANGUARDS + RAID_HOME_RESERVE_RANGERS
)
RAID_SWEEP_INITIAL_RADIUS = 18
RAID_SWEEP_RING_SPACING = 8
RAID_SWEEP_WAYPOINT_REACHED_RADIUS = 4
RAID_CORE_GUARD_RADIUS = 8
RAID_STATIONARY_OBSERVATIONS = 3
RAID_ENEMY_MOTION_MAX_AGE = 16
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
CORE_DIRECTION_COMMIT_TICKS = 8
RANGER_DEFENSE_LEASH_RADIUS = 8
CORE_PATROL_RANGER_COUNT = 2
CORE_PATROL_RADIUS = 2
CORE_PATROL_ROTATION_TICKS = 8
# 射失后的短期记忆：避免对同一敌人和同一格连续浪费行动。
RANGER_SHOT_MISS_MEMORY_TICKS = 8
DEFENSE_REPLACEMENT_RESERVE = 10
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
MIGRATION_SITE_TOTAL_ATTACK_CELLS = 24
MIGRATION_SITE_RANGED_ATTACK_CELLS = 16
MIGRATION_SITE_MAX_OPEN_RANGED_CELLS = 12
MIGRATION_SITE_MIN_OPEN_HALF_RATIO_NUMERATOR = 3
MIGRATION_SITE_MIN_OPEN_HALF_RATIO_DENOMINATOR = 4
MIGRATION_ESCORT_RADIUS = 7
MIGRATION_MIN_ESCORTS = 4


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


@dataclass(frozen=True)
class RaidEnemyMotion:
    position: Position
    stationary_observations: int
    last_seen_tick: int


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
    recall: bool = False
    migration_candidate: Position | None = None
    migration_target: Position | None = None
    migration_site_checked: bool = False
    migration_site_score: int = 0
    auto_migrate: bool = False
    unit_label_mapping: dict[str, str] = field(default_factory=dict)
    last_events: list[dict] = field(default_factory=list)
    unit_positions_for_overlay: dict[str, Position] = field(default_factory=dict)
    last_tick: int = 0
    beacon_target_distance: int = 0
    rally_point: tuple[int, int] | None = None
    raid_enabled: bool = False
    raid_recall: bool = False
    raid_vanguards: int = 0
    raid_rangers: int = 0
    raid_vanguard_ids: set[str] = field(default_factory=set)
    raid_ranger_ids: set[str] = field(default_factory=set)
    raid_sweep_origin: Position | None = None
    raid_sweep_steps: dict[str, int] = field(default_factory=dict)
    raid_core_id: str | None = None
    raid_core_position: Position | None = None
    raid_core_acquired_tick: int = 0
    raid_enemy_motion: dict[str, RaidEnemyMotion] = field(default_factory=dict)
    # 闪电模式状态：方环 (inner_r, outer_r)、当前巡逻点、轮转相位、每单位 claim 的敌方 Core、扇区分配。
    lightning_ring: tuple = field(default_factory=lambda: tuple(LIGHTNING_DEFAULT_RING))
    lightning_patrol_waypoint: tuple[int, int] | None = None
    lightning_patrol_phase: int = 0
    lightning_claims: dict[str, str] = field(default_factory=dict)
    # 判定为重兵把守而永久放弃的敌方 Core UUID 集合。世界很大、复活磁铁会不断
    # 送来新的无护卫 Core，没必要死磕被围住的。acquire 永久跳过这些 ID。
    lightning_blacklist: set[str] = field(default_factory=set)
    lightning_sectors: dict[str, tuple[int, int]] = field(default_factory=dict)
    # 游侠并排探路：UUID → 固定 lane index（径向周界半径的偏移档位）。
    lightning_scout_lanes: dict[str, int] = field(default_factory=dict)
    # 游侠独立绕圈游标：UUID → 当前周界角序号(0..3)，到达角死区后推进。
    # 与 Core 位置解耦——游侠沿自己 lane 的周界独立绕圈，不等 Core。
    lightning_scout_phase: dict[str, int] = field(default_factory=dict)
    # 先锋 V 字纵深状态机：UUID → {phase: "OUT"/"IN", leg: 0/1, origin: (x,y), target: (x,y)}。
    lightning_vee_state: dict[str, dict] = field(default_factory=dict)
    # 绕 Core 转的行星轨道（近/中/远）每单位周界角序号(0..3)，到角死区后推进。
    # 与绕原点的 scout/breakthrough 区分：圆心是 core.position 而非 (0,0)。
    lightning_orbit_phase: dict[str, int] = field(default_factory=dict)
    # 开路轨道（绕原点外大环）游侠的周界角序号(0..3)。前 4 个游侠填此槽。
    # 复用 lightning_scout_phase 的相位机制，但半径档更大（开路环）。
    lightning_breakthrough_phase: dict[str, int] = field(default_factory=dict)
    # 行星轨道 lane 分配缓存：role(str) → UUID → lane index，随死亡剪枝重排。
    lightning_orbit_lanes: dict[str, dict[str, int]] = field(default_factory=dict)
    # 鬼打墙逃生：UUID → 连续"小范围震荡"检测计数（达阈值触发逃生）。
    lightning_unit_stuck_counters: dict[str, int] = field(default_factory=dict)
    # 鬼打墙逃生：UUID → 逃生模式截止 tick。逃生期间忽略巡逻目标，
    # 只往"开阔 + 低 visited 密度"方向走，强制脱出障碍死角。
    lightning_unit_escape_until: dict[str, int] = field(default_factory=dict)
    attacked_units: dict[str, int] = field(default_factory=dict)
    replacement_queue: Counter[str] = field(default_factory=Counter)
    control_mtime: int = 0
    total_resources_harvested: int = 0
    total_resources_deposited: int = 0
    total_resources_captured: int = 0
    enemy_cores_destroyed: int = 0
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
    shot_miss_counts: Counter[str] = field(default_factory=Counter, repr=False)
    shot_miss_ticks: dict[str, int] = field(default_factory=dict, repr=False)
    current_shot_cells: set[tuple[str, Position]] = field(
        default_factory=set,
        repr=False,
    )

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
                )
                for object_id, value in data.get("enemy_sightings", {}).items()
                if isinstance(value, list) and len(value) == 4
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
            migration_candidate = data.get("migration_candidate")
            if isinstance(migration_candidate, list) and len(migration_candidate) == 2:
                memory.migration_candidate = (
                    int(migration_candidate[0]),
                    int(migration_candidate[1]),
                )
            migration_target = data.get("migration_target")
            if isinstance(migration_target, list) and len(migration_target) == 2:
                memory.migration_target = (
                    int(migration_target[0]),
                    int(migration_target[1]),
                )
            memory.migration_site_checked = bool(
                data.get("migration_site_checked", False)
            )
            memory.migration_site_score = max(
                0,
                int(data.get("migration_site_score", 0)),
            )
            memory.auto_migrate = bool(data.get("auto_migrate", False))
            memory.last_tick = int(data.get("last_tick", 0))
            memory.recall = bool(data.get("recall", False))
            memory.raid_enabled = bool(data.get("raid_enabled", False))
            memory.raid_recall = bool(data.get("raid_recall", False))
            memory.raid_vanguards = max(
                0,
                int(data.get("raid_vanguards", DEFAULT_RAID_VANGUARDS)),
            )
            memory.raid_rangers = max(
                0,
                int(data.get("raid_rangers", DEFAULT_RAID_RANGERS)),
            )
            memory.raid_vanguard_ids = {
                str(unit_id)
                for unit_id in data.get("raid_vanguard_ids", ())
                if unit_id
            }
            memory.raid_ranger_ids = {
                str(unit_id)
                for unit_id in data.get("raid_ranger_ids", ())
                if unit_id
            }
            raid_origin = data.get("raid_sweep_origin")
            if isinstance(raid_origin, list) and len(raid_origin) == 2:
                memory.raid_sweep_origin = (int(raid_origin[0]), int(raid_origin[1]))
            memory.raid_sweep_steps = {
                str(unit_id): max(0, int(step))
                for unit_id, step in data.get("raid_sweep_steps", {}).items()
            }
            raid_core_id = data.get("raid_core_id")
            memory.raid_core_id = str(raid_core_id) if raid_core_id else None
            raid_core_position = data.get("raid_core_position")
            if (
                isinstance(raid_core_position, list)
                and len(raid_core_position) == 2
            ):
                memory.raid_core_position = (
                    int(raid_core_position[0]),
                    int(raid_core_position[1]),
                )
            memory.raid_core_acquired_tick = max(
                0,
                int(data.get("raid_core_acquired_tick", 0)),
            )
            memory.raid_enemy_motion = {
                str(enemy_id): RaidEnemyMotion(
                    position=(int(value[0]), int(value[1])),
                    stationary_observations=max(1, int(value[2])),
                    last_seen_tick=max(0, int(value[3])),
                )
                for enemy_id, value in data.get("raid_enemy_motion", {}).items()
                if isinstance(value, list) and len(value) == 4
            }
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
            memory.first_observed_tick = int(data.get("first_observed_tick", 0))
            memory.observed_turns = int(data.get("observed_turns", 0))
            memory.units_lost = int(data.get("units_lost", 0))
            raw_ring = data.get("lightning_ring")
            if isinstance(raw_ring, list) and len(raw_ring) == 2:
                inner_r, outer_r = int(raw_ring[0]), int(raw_ring[1])
                if outer_r >= inner_r > 0:
                    memory.lightning_ring = (inner_r, outer_r)
            raw_waypoint = data.get("lightning_patrol_waypoint")
            if isinstance(raw_waypoint, list) and len(raw_waypoint) == 2:
                memory.lightning_patrol_waypoint = (
                    int(raw_waypoint[0]),
                    int(raw_waypoint[1]),
                )
            memory.lightning_patrol_phase = int(
                data.get("lightning_patrol_phase", 0)
            )
            memory.lightning_claims = {
                str(unit_id): str(core_id)
                for unit_id, core_id in data.get("lightning_claims", {}).items()
            }
            memory.lightning_blacklist = {
                str(core_id) for core_id in data.get("lightning_blacklist", ())
            }
            raw_sectors = data.get("lightning_sectors", {})
            memory.lightning_sectors = {
                str(unit_id): (int(sector[0]), int(sector[1]))
                for unit_id, sector in raw_sectors.items()
                if isinstance(sector, list) and len(sector) == 2
            }
            memory.lightning_scout_lanes = {
                str(unit_id): int(lane)
                for unit_id, lane in data.get("lightning_scout_lanes", {}).items()
            }
            memory.lightning_scout_phase = {
                str(unit_id): int(phase) % 4
                for unit_id, phase in data.get("lightning_scout_phase", {}).items()
            }
            memory.lightning_vee_state = {}
            for unit_id, state in data.get("lightning_vee_state", {}).items():
                if not isinstance(state, dict):
                    continue
                origin = state.get("origin")
                target = state.get("target")
                if (
                    isinstance(origin, list)
                    and len(origin) == 2
                    and isinstance(target, list)
                    and len(target) == 2
                ):
                    memory.lightning_vee_state[str(unit_id)] = {
                        "phase": str(state.get("phase", "OUT")),
                        "leg": int(state.get("leg", 0)),
                        "origin": (int(origin[0]), int(origin[1])),
                        "target": (int(target[0]), int(target[1])),
                    }
            memory.lightning_orbit_phase = {
                str(unit_id): int(phase) % 4
                for unit_id, phase in data.get("lightning_orbit_phase", {}).items()
            }
            memory.lightning_breakthrough_phase = {
                str(unit_id): int(phase) % 4
                for unit_id, phase in data.get("lightning_breakthrough_phase", {}).items()
            }
            raw_orbit_lanes = data.get("lightning_orbit_lanes", {})
            memory.lightning_orbit_lanes = {
                str(role): {
                    str(uid): int(lane)
                    for uid, lane in lanes.items()
                }
                for role, lanes in raw_orbit_lanes.items()
                if isinstance(lanes, dict)
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
            "migration_candidate": (
                list(self.migration_candidate)
                if self.migration_candidate is not None
                else None
            ),
            "migration_target": (
                list(self.migration_target)
                if self.migration_target is not None
                else None
            ),
            "migration_site_checked": self.migration_site_checked,
            "migration_site_score": self.migration_site_score,
            "auto_migrate": self.auto_migrate,
            "last_tick": self.last_tick,
            "recall": self.recall,
            "raid_enabled": self.raid_enabled,
            "raid_recall": self.raid_recall,
            "raid_vanguards": self.raid_vanguards,
            "raid_rangers": self.raid_rangers,
            "raid_vanguard_ids": sorted(self.raid_vanguard_ids),
            "raid_ranger_ids": sorted(self.raid_ranger_ids),
            "raid_sweep_origin": (
                list(self.raid_sweep_origin)
                if self.raid_sweep_origin is not None
                else None
            ),
            "raid_sweep_steps": dict(sorted(self.raid_sweep_steps.items())),
            "raid_core_id": self.raid_core_id,
            "raid_core_position": (
                list(self.raid_core_position)
                if self.raid_core_position is not None
                else None
            ),
            "raid_core_acquired_tick": self.raid_core_acquired_tick,
            "raid_enemy_motion": {
                enemy_id: [
                    motion.position[0],
                    motion.position[1],
                    motion.stationary_observations,
                    motion.last_seen_tick,
                ]
                for enemy_id, motion in sorted(self.raid_enemy_motion.items())
            },
            "replacement_queue": dict(sorted(self.replacement_queue.items())),
            "total_resources_harvested": self.total_resources_harvested,
            "total_resources_deposited": self.total_resources_deposited,
            "total_resources_captured": self.total_resources_captured,
            "enemy_cores_destroyed": self.enemy_cores_destroyed,
            "first_observed_tick": self.first_observed_tick,
            "observed_turns": self.observed_turns,
            "units_lost": self.units_lost,
            "lightning_ring": [
                self.lightning_ring[0],
                self.lightning_ring[1],
            ],
            "lightning_patrol_waypoint": (
                [
                    self.lightning_patrol_waypoint[0],
                    self.lightning_patrol_waypoint[1],
                ]
                if self.lightning_patrol_waypoint is not None
                else None
            ),
            "lightning_patrol_phase": self.lightning_patrol_phase,
            "lightning_claims": dict(sorted(self.lightning_claims.items())),
            "lightning_blacklist": sorted(self.lightning_blacklist),
            "lightning_sectors": {
                unit_id: [sector[0], sector[1]]
                for unit_id, sector in sorted(self.lightning_sectors.items())
            },
            "lightning_scout_lanes": dict(sorted(self.lightning_scout_lanes.items())),
            "lightning_scout_phase": dict(sorted(self.lightning_scout_phase.items())),
            "lightning_vee_state": {
                unit_id: {
                    "phase": state["phase"],
                    "leg": state["leg"],
                    "origin": [state["origin"][0], state["origin"][1]],
                    "target": [state["target"][0], state["target"][1]],
                }
                for unit_id, state in sorted(self.lightning_vee_state.items())
            },
            "lightning_orbit_phase": dict(sorted(self.lightning_orbit_phase.items())),
            "lightning_breakthrough_phase": dict(
                sorted(self.lightning_breakthrough_phase.items())
            ),
            "lightning_orbit_lanes": {
                role: dict(sorted(lanes.items()))
                for role, lanes in sorted(self.lightning_orbit_lanes.items())
            },
            "lightning_unit_stuck_counters": dict(
                sorted(self.lightning_unit_stuck_counters.items())
            ),
            "lightning_unit_escape_until": dict(
                sorted(self.lightning_unit_escape_until.items())
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
                self.clear_raid_state()
                self.clear_local_core_sortie()
            elif event.event_type == "CORE_RESPAWNED":
                self.last_core_respawn_tick = turn.tick
                self.clear_core_shelter_memory()
                self.core_heading = None
                self.last_core_move_tick = 0
                self.clear_raid_state()
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
        visible_enemy_ids = {str(enemy.id) for enemy in turn.visible_enemies}
        if visible_enemy_ids:
            self.last_enemy_visible_tick = turn.tick
        for enemy in turn.visible_enemies:
            self.enemy_sightings[str(enemy.id)] = EnemySighting(
                position=enemy.position,
                seen_tick=turn.tick,
                is_core=isinstance(enemy, CoreView),
            )
        # 清理 enemy_sightings：(1)非 Core 且超过 ASSAULT_SIGHTING_MAX_AGE，
        # (2)当前可见位置但物体不在（确认消失），(3)Core sighting 超过
        # LIGHTNING_SIGHTING_MAX_AGE（防止陈旧 Core 记录导致永久 hunt）。
        self.enemy_sightings = {
            object_id: sighting
            for object_id, sighting in self.enemy_sightings.items()
            if (
                (
                    sighting.is_core
                    and turn.tick - sighting.seen_tick <= LIGHTNING_SIGHTING_MAX_AGE
                )
                or (
                    not sighting.is_core
                    and turn.tick - sighting.seen_tick <= ASSAULT_SIGHTING_MAX_AGE
                )
            )
            and not (
                object_id not in visible_enemy_ids
                and _currently_visible(turn, sighting.position, self.known_obstacles)
            )
        }
        # 清理指向已过期 sighting 的 claims 和 blacklist
        valid_core_ids = {
            oid for oid, s in self.enemy_sightings.items() if s.is_core
        }
        self.lightning_claims = {
            uid: core_id
            for uid, core_id in self.lightning_claims.items()
            if core_id in valid_core_ids
        }
        self.lightning_blacklist = {
            core_id for core_id in self.lightning_blacklist if core_id in valid_core_ids
        }
        for position in turn.resource_cells:
            self.resource_last_seen[position] = turn.tick

        for position in tuple(self.recovery_targets):
            if (
                position != self.migration_candidate
                and
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
            if isinstance(enemy, CoreView):
                self.enemy_prev.pop(eid, None)
            if isinstance(enemy, UnitView):
                previous_motion = self.raid_enemy_motion.get(eid)
                consecutive = (
                    previous_motion is not None
                    and previous_motion.last_seen_tick == turn.tick - 1
                    and previous_motion.position == enemy.position
                )
                self.raid_enemy_motion[eid] = RaidEnemyMotion(
                    position=enemy.position,
                    stationary_observations=(
                        previous_motion.stationary_observations + 1
                        if consecutive and previous_motion is not None
                        else 1
                    ),
                    last_seen_tick=turn.tick,
                )
        for eid in list(self.enemy_positions):
            if eid not in {str(e.id) for e in turn.visible_enemies}:
                self.enemy_positions.pop(eid, None)
                self.enemy_prev.pop(eid, None)
        visible_motion_ids = {
            str(enemy.id)
            for enemy in turn.visible_enemies
            if isinstance(enemy, UnitView)
        }
        self.raid_enemy_motion = {
            enemy_id: motion
            for enemy_id, motion in self.raid_enemy_motion.items()
            if (
                enemy_id in visible_motion_ids
                or turn.tick - motion.last_seen_tick <= RAID_ENEMY_MOTION_MAX_AGE
            )
        }
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

    def clear_raid_state(self) -> None:
        self.raid_vanguard_ids.clear()
        self.raid_ranger_ids.clear()
        self.raid_sweep_steps.clear()
        self.raid_sweep_origin = None
        self.raid_core_id = None
        self.raid_core_position = None
        self.raid_core_acquired_tick = 0
        self.raid_enemy_motion.clear()

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
            self.recall = bool(data.get("recall", self.recall))
            previous_raid_enabled = self.raid_enabled
            self.raid_enabled = bool(data.get("raid_enabled", self.raid_enabled))
            self.raid_recall = bool(data.get("raid_recall", self.raid_recall))
            for key in ("raid_vanguards", "raid_rangers"):
                raw_value = data.get(key)
                if isinstance(raw_value, (int, float)) and not isinstance(
                    raw_value,
                    bool,
                ):
                    setattr(self, key, max(0, int(raw_value)))
            if previous_raid_enabled != self.raid_enabled:
                self.raid_vanguard_ids.clear()
                self.raid_ranger_ids.clear()
                self.raid_sweep_steps.clear()
                self.raid_sweep_origin = None
                self.raid_core_id = None
                self.raid_core_position = None
                self.raid_core_acquired_tick = 0
            if not self.raid_enabled:
                self.raid_vanguard_ids.clear()
                self.raid_ranger_ids.clear()
                self.raid_sweep_steps.clear()
                self.raid_sweep_origin = None
                self.raid_core_id = None
                self.raid_core_position = None
                self.raid_core_acquired_tick = 0
            previous_candidate = self.migration_candidate
            raw_candidate = data.get("migration_candidate")
            if (
                isinstance(raw_candidate, list)
                and len(raw_candidate) == 2
                and all(
                    isinstance(value, (int, float)) and not isinstance(value, bool)
                    for value in raw_candidate
                )
            ):
                self.migration_candidate = (
                    int(raw_candidate[0]),
                    int(raw_candidate[1]),
                )
            else:
                self.migration_candidate = None
            self.auto_migrate = bool(data.get("auto_migrate", self.auto_migrate))
            if self.migration_candidate != previous_candidate:
                if previous_candidate is not None:
                    self.recovery_targets = [
                        position
                        for position in self.recovery_targets
                        if position != previous_candidate
                    ]
                    self.worker_goals = {
                        unit_id: goal
                        for unit_id, goal in self.worker_goals.items()
                        if not (
                            goal.kind == "resource_recovery"
                            and goal.position == previous_candidate
                        )
                    }
                if self.migration_candidate is not None:
                    self.recovery_checked.discard(self.migration_candidate)
                    if self.migration_candidate not in self.recovery_targets:
                        self.recovery_targets.append(self.migration_candidate)
                self.migration_site_checked = False
                self.migration_site_score = 0
                self.migration_target = None
            raw_distance = data.get("beacon_target_distance")
            if isinstance(raw_distance, (int, float)) and not isinstance(
                raw_distance, bool
            ):
                self.beacon_target_distance = max(0, int(raw_distance))
            raw_rally = data.get("rally_point")
            if (
                isinstance(raw_rally, list)
                and len(raw_rally) == 2
                and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in raw_rally)
            ):
                self.rally_point = (int(raw_rally[0]), int(raw_rally[1]))
            else:
                self.rally_point = None
            for key in ("aggress_vanguards", "aggress_rangers"):
                raw_value = data.get(key)
                if isinstance(raw_value, (int, float)) and not isinstance(
                    raw_value, bool
                ):
                    setattr(self, key, max(0, int(raw_value)))
            raw_ring = data.get("lightning_ring")
            if (
                isinstance(raw_ring, list)
                and len(raw_ring) == 2
                and all(
                    isinstance(v, (int, float)) and not isinstance(v, bool)
                    for v in raw_ring
                )
            ):
                inner_r = int(raw_ring[0])
                outer_r = int(raw_ring[1])
                if outer_r >= inner_r > 0:
                    self.lightning_ring = (inner_r, outer_r)
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
        if (
            self.migration_candidate is not None
            and self.migration_site_checked
            and self.migration_target is None
        ):
            rejected_candidate = self.migration_candidate
            self.recovery_targets = [
                position
                for position in self.recovery_targets
                if position != rejected_candidate
            ]
            self.worker_goals = {
                unit_id: goal
                for unit_id, goal in self.worker_goals.items()
                if not (
                    goal.kind == "resource_recovery"
                    and goal.position == rejected_candidate
                )
            }
            self.recovery_checked.add(rejected_candidate)
        if self.migration_candidate is not None and not self.migration_site_checked:
            self.recovery_checked.discard(self.migration_candidate)
            configured_set.add(self.migration_candidate)
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
        if (
            self.migration_candidate is not None
            and self.migration_candidate not in ordered_targets
        ):
            ordered_targets.append(self.migration_candidate)
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
                "tick": turn.tick,
                "recall": self.recall,
                "raid_enabled": self.raid_enabled,
                "raid_recall": self.raid_recall,
                "raid_vanguards": self.raid_vanguards,
                "raid_rangers": self.raid_rangers,
                "raid_selected_vanguards": len(self.raid_vanguard_ids),
                "raid_selected_rangers": len(self.raid_ranger_ids),
                "raid_core_position": (
                    list(self.raid_core_position)
                    if self.raid_core_position is not None
                    else None
                ),
                "raid_core_acquired_tick": self.raid_core_acquired_tick,
                "raid_sweep_radius": max(
                    (
                        RAID_SWEEP_INITIAL_RADIUS
                        + (step // len(ASSAULT_SWEEP_SECTOR_OFFSETS))
                        * RAID_SWEEP_RING_SPACING
                        for step in self.raid_sweep_steps.values()
                    ),
                    default=RAID_SWEEP_INITIAL_RADIUS,
                ),
                "migration_candidate": (
                    list(self.migration_candidate)
                    if self.migration_candidate is not None
                    else None
                ),
                "migration_target": (
                    list(self.migration_target)
                    if self.migration_target is not None
                    else None
                ),
                "migration_site_checked": self.migration_site_checked,
                "migration_site_score": self.migration_site_score,
                "beacon_target_distance": self.beacon_target_distance,
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
                        < RAID_HOME_RESERVE_COMBAT
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
                "shoot_count": self.decision_totals.get("ranger:shoot", 0),
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


def _shot_cell_key(target_id: UUID, cell: Position) -> str:
    return f"{target_id}|{cell[0]}|{cell[1]}"


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

    def choose_actions(self, turn: Turn) -> DecisionSummary:
        self.memory.load_control(self.control_path)
        self.memory.refresh_recovery_target_hints()
        self.memory.refresh_browser_intel()
        self.memory.observe(turn)
        # 只在本 Tick 内协调多名游侠的覆盖格，不把未来 Tick 的动作带入。
        self.memory.current_shot_cells.clear()
        previous_events = Counter(event.event_type for event in turn.events)
        decisions = list(self.memory.observations)

        if turn.core is None:
            return self._summary(turn, previous_events, decisions)

        planner = MovementPlanner(turn, self.memory, decisions)
        acted_units: set[UUID] = set()

        # 闪电模式：工人采集 → 治疗 → 战斗单位移动/攻击 → Core 巡逻
        incoming_deposit = self._choose_workers(turn, planner, acted_units, decisions)
        self._choose_healing(turn, planner, acted_units, decisions)
        self._choose_vanguards(turn, planner, acted_units, decisions)
        self._choose_rangers(turn, planner, acted_units, decisions)
        self._choose_core(turn, planner, False, incoming_deposit, decisions)
        return self._summary(turn, previous_events, decisions)

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

    def _maybe_activate_migration(self, turn: Turn) -> None:
        candidate = self.memory.migration_candidate
        if (
            candidate is None
            or not self.memory.auto_migrate
            or self.memory.migration_site_checked
            or False
            or True
            or not any(unit.position == candidate for unit in turn.units)
        ):
            return

        obstacles = set(turn.obstacle_cells)
        open_count, open_axis, concentrated_count, melee_open = (
            _core_attack_surface_profile(candidate, obstacles)
        )
        score = MIGRATION_SITE_RANGED_ATTACK_CELLS - open_count
        suitable = (
            candidate not in obstacles
            and open_count <= MIGRATION_SITE_MAX_OPEN_RANGED_CELLS
            and concentrated_count * MIGRATION_SITE_MIN_OPEN_HALF_RATIO_DENOMINATOR
            >= open_count * MIGRATION_SITE_MIN_OPEN_HALF_RATIO_NUMERATOR
        )
        self.memory.migration_site_checked = True
        self.memory.migration_site_score = score
        if not suitable:
            self.memory.complete_recovery_target(candidate, "migration_site_rejected")
            self.memory.observations.append(
                "migration_site_rejected "
                f"target={candidate} attack_model="
                f"{MIGRATION_SITE_TOTAL_ATTACK_CELLS} "
                f"ranged_attack={open_count}/"
                f"{MIGRATION_SITE_RANGED_ATTACK_CELLS} "
                f"dominant_half={concentrated_count} axis={open_axis} "
                f"melee_open={melee_open}/8"
            )
            self.memory.decision_totals["migration:site_rejected"] += 1
            return

        # mode assignment removed (Lightning only)
        self.memory.recall = False
        self.memory.migration_target = candidate
        self.memory.complete_recovery_target(candidate, "migration_site_confirmed")
        self.memory.observations.append(
            "migration_site_confirmed "
            f"target={candidate} attack_model="
            f"{MIGRATION_SITE_TOTAL_ATTACK_CELLS} "
            f"ranged_attack={open_count}/"
            f"{MIGRATION_SITE_RANGED_ATTACK_CELLS} "
            f"dominant_half={concentrated_count} axis={open_axis} "
            f"melee_open={melee_open}/8 mode=migrate"
        )
        self.memory.decision_totals["migration:site_confirmed"] += 1
        try:
            data = json.loads(self.control_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                data = {}
            data["mode"] = MODE_MIGRATE
            data["recall"] = False
            temporary = self.control_path.with_suffix(self.control_path.suffix + ".tmp")
            temporary.write_text(
                json.dumps(data, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            temporary.replace(self.control_path)
            self.memory.control_mtime = self.control_path.stat().st_mtime_ns
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            self.memory.observations.append(
                "migration_control_update_failed mode_retained_in_memory"
            )

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

        # Phase 2: 工人肉盾逻辑 - 检测MID/NEAR威胁
        near_threat_radius = 6  # NEAR tier
        mid_threat_radius = 20  # MID tier

        # 统计Core附近的敌方战斗单位距离
        combat_enemies = [
            enemy for enemy in turn.visible_enemies
            if isinstance(enemy, UnitView)
            and enemy.unit_type in {UnitType.VANGUARD, UnitType.RANGER}
        ]

        nearest_enemy_dist = float('inf')
        if combat_enemies:
            nearest_enemy_dist = min(
                _distance(turn.core.position, enemy.position)
                for enemy in combat_enemies
            )

        # NEAR威胁：所有空闲工人立即回防近轨道当肉盾
        workers_need_defend = (
            nearest_enemy_dist <= near_threat_radius
            or nearest_enemy_dist <= mid_threat_radius
        )

        for worker in sorted(turn.workers, key=_uuid_key):
            if worker.id in acted_units:
                continue

            # Phase 2: 工人肉盾行为优先级最高
            if workers_need_defend and not worker.cargo:
                # 空手工人回到近轨道（r=5）当肉盾
                near_orbit_radius = 5
                core_pos = turn.core.position

                # 计算最近的近轨道点
                current_dist = _distance(worker.position, core_pos)
                if current_dist > near_orbit_radius + 2:  # 距离近轨道较远
                    # 向Core方向移动
                    if planner.toward(worker, core_pos, "worker_meatshield_defend"):
                        decisions.append(
                            f"worker:{_short_id(worker.id)} meatshield_defend "
                            f"enemy_dist={nearest_enemy_dist} moving_to_core"
                        )
                        self.memory.decision_totals["worker:meatshield_defend"] += 1
                        acted_units.add(worker.id)
                        continue
                # 已在近轨道附近，保持位置或微调
                elif current_dist <= near_orbit_radius + 2:
                    decisions.append(
                        f"worker:{_short_id(worker.id)} meatshield_hold "
                        f"enemy_dist={nearest_enemy_dist} pos={worker.position}"
                    )
                    self.memory.decision_totals["worker:meatshield_hold"] += 1
                    acted_units.add(worker.id)
                    continue

            if worker.cargo:
                self.memory.clear_worker_goal(worker)
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

            if planner.threat.get(worker.position, 0) > 0:
                threats = [
                    enemy.position
                    for enemy in turn.visible_enemies
                    if _distance(worker.position, enemy.position) <= 3
                ]
                if threats and planner.flee(worker, threats, "worker_flee"):
                    if (
                        False
                        and not owns_beacon
                        and _distance(worker.position, turn.core.position)
                        > DEVELOP_WIDE_SEARCH_MAX_RADIUS
                    ):
                        recall_goal = self.memory.worker_goals.get(str(worker.id))
                        if not (
                            recall_goal is not None
                            and recall_goal.kind == "develop_local_recall"
                            and recall_goal.position == turn.core.position
                        ):
                            self.memory.set_worker_goal(
                                worker,
                                "develop_local_recall",
                                turn.core.position,
                                turn.tick,
                            )
                            decisions.append(
                                f"worker:{_short_id(worker.id)} "
                                "remote_threat_recall"
                            )
                            self.memory.decision_totals[
                                "worker:remote_threat_recall"
                            ] += 1
                    else:
                        self.memory.clear_worker_goal(worker)
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
                    and not (
                        not self.memory.migration_site_checked
                        and (goal := self.memory.worker_goals.get(str(worker.id)))
                        is not None
                        and goal.kind == "resource_recovery"
                        and goal.position == self.memory.migration_candidate
                    )
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
            # 工人远行星轨道：发现资源 → 现有经济逻辑(上方已处理采集/回仓)；
            # 空闲(无货、无资源目标) → 上远行星轨道绕 Core 外圈转圈巡逻(工人距
            # Core 最外层,游侠中轨内圈),点亮外围迷雾防敌方钻空子。分层见 _lightning_orbit_lane_radius。
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
        raid_ids = self.memory.raid_vanguard_ids | self.memory.raid_ranger_ids
        available_reliefs = [
            unit
            for unit in (*turn.vanguards, *turn.rangers)
            if unit.id not in beacon_guard_ids
            and str(unit.id) not in reserved_ids
            and str(unit.id) not in raid_ids
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
            raid_reserve: int,
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
                    len(pool) - configured_attackers - raid_reserve,
                )
            else:
                count = min(
                    default_defenders,
                    len(pool) if carrier is not None else max(0, len(pool) - 1),
                )
                if (
                    raid_reserve > 0
                    or len(pool) >= default_defenders + minimum_attackers
                ):
                    count = min(
                        count,
                        max(0, len(pool) - raid_reserve - minimum_attackers),
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
            self.memory.raid_vanguards if self.memory.raid_enabled else 0,
            AGGRESS_MIN_ASSAULT_VANGUARDS,
            UnitType.VANGUARD,
        )
        ranger_defenders = assigned_defenders(
            turn.rangers,
            set(beacon_ranger_guards),
            self.memory.aggress_rangers,
            1,  # 原 AGGRESS_DEFENDER_RANGERS
            self.memory.raid_rangers if self.memory.raid_enabled else 0,
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
            or len(turn.vanguards) < RAID_HOME_RESERVE_VANGUARDS
            or len(turn.rangers) < RAID_HOME_RESERVE_RANGERS
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
            or len(turn.vanguards) < RAID_HOME_RESERVE_VANGUARDS
            or len(turn.rangers) < RAID_HOME_RESERVE_RANGERS
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
            or len(turn.vanguards) < RAID_HOME_RESERVE_VANGUARDS
            or len(turn.rangers) < RAID_HOME_RESERVE_RANGERS
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
        vanguard_shortfall = max(0, RAID_HOME_RESERVE_VANGUARDS - len(turn.vanguards))
        ranger_shortfall = max(0, RAID_HOME_RESERVE_RANGERS - len(turn.rangers))
        combat_shortfall = max(
            0,
            RAID_HOME_RESERVE_COMBAT - (len(turn.vanguards) + len(turn.rangers)),
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

    def _maybe_activate_beacon_expedition(self, turn: Turn) -> None:
        """Send only surplus combat units after the fixed home reserve is safe."""
        if (
            True
            or self.memory.recall
            or _owns_beacon(turn)
            or self._home_recovery_active(turn)
            or turn.core is None
            or any(
                _distance(turn.core.position, enemy.position)
                <= CORE_EMERGENCY_THREAT_RADIUS
                for enemy in turn.visible_enemies
            )
        ):
            return
        required_vanguards = (
            RAID_HOME_RESERVE_VANGUARDS + DEVELOP_BEACON_EXPEDITION_VANGUARDS
        )
        required_rangers = (
            RAID_HOME_RESERVE_RANGERS + DEVELOP_BEACON_EXPEDITION_RANGERS
        )
        if (
            len(turn.vanguards) < required_vanguards
            or len(turn.rangers) < required_rangers
        ):
            return
        # mode assignment removed (Lightning only)
        self.memory.observations.append(
            "beacon_expedition_activated "
            f"vanguards={len(turn.vanguards)} rangers={len(turn.rangers)}"
        )
        self.memory.decision_totals["beacon:expedition_activated"] += 1
        try:
            data = json.loads(self.control_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                data = {}
            data["mode"] = MODE_BEACON
            temporary = self.control_path.with_suffix(
                self.control_path.suffix + ".tmp"
            )
            temporary.write_text(
                json.dumps(data, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            temporary.replace(self.control_path)
            self.memory.control_mtime = self.control_path.stat().st_mtime_ns
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            self.memory.observations.append(
                "beacon_expedition_control_update_failed mode_retained_in_memory"
            )

    def _develop_beacon_scout_ids(
        self,
        turn: Turn,
    ) -> tuple[set[UUID], set[UUID]]:
        """Release a stable 1+1 head-start pair without switching workers to Beacon mode."""
        if (
            True
            or self.memory.recall
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
            unit.id for unit in vanguard_pool[:RAID_HOME_RESERVE_VANGUARDS]
        }
        reserved_rangers = {
            unit.id for unit in ranger_pool[:RAID_HOME_RESERVE_RANGERS]
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
            len(home_vanguards) >= RAID_HOME_RESERVE_VANGUARDS
            and len(home_rangers) >= RAID_HOME_RESERVE_RANGERS
        )
        if self._home_recovery_active(turn) and not reserve_complete:
            return set(), set()
        return home_vanguards, home_rangers

    def _beacon_core_assault_target(
        self,
        turn: Turn,
        home_vanguards: set[UUID],
        home_rangers: set[UUID],
    ) -> Position | None:
        """Use only Beacon-expedition surplus to pursue a known enemy Core."""
        emergency_threats = self._core_emergency_threats(turn)
        home_defender_count = len(home_vanguards) + len(home_rangers)
        home_screen_ready = (
            len(home_vanguards) >= RAID_HOME_RESERVE_VANGUARDS
            and len(home_rangers) >= RAID_HOME_RESERVE_RANGERS
        )
        home_screen_can_contain_threat = (
            home_screen_ready
            and len(emergency_threats) < home_defender_count
        )
        if (
            True
            or turn.core is None
            or self._core_recently_damaged(turn)
            or (
                self._home_recovery_active(turn)
                and not home_screen_can_contain_threat
            )
        ):
            return None
        assault_vanguards = sum(
            unit.id not in home_vanguards for unit in turn.vanguards
        )
        assault_rangers = sum(unit.id not in home_rangers for unit in turn.rangers)
        if (
            assault_vanguards < CORE_ASSAULT_MIN_VANGUARDS
            or assault_rangers < CORE_ASSAULT_MIN_RANGERS
        ):
            return None
        return self._pick_enemy_core_target(turn)

    def _beacon_local_core_sortie_assignments(
        self,
        turn: Turn,
        home_vanguards: set[UUID],
        home_rangers: set[UUID],
        decisions: list[str],
    ) -> tuple[Position | None, set[UUID], set[UUID]]:
        """Borrow 1V+2R from a complete home screen for a safe local Core kill."""
        active = self.memory.local_core_sortie_core_id is not None

        def cancel(reason: str) -> None:
            if self.memory.local_core_sortie_core_id is None:
                return
            decisions.append(
                "local_core_sortie_cancelled "
                f"target={self.memory.local_core_sortie_position} reason={reason}"
            )
            self.memory.decision_totals[
                f"local_core_sortie:cancel:{reason}"
            ] += 1
            self.memory.clear_local_core_sortie()

        unsafe_home = (
            True
            or self.memory.recall
            or turn.core is None
            or self._home_recovery_active(turn)
            or bool(self._core_emergency_threats(turn))
            or self._core_recently_damaged(turn)
            or self._core_recently_reset(turn)
        )
        if unsafe_home:
            cancel("home_unsafe")
            return None, set(), set()

        visible_cores = {
            str(enemy.id): enemy
            for enemy in turn.visible_enemies
            if isinstance(enemy, CoreView)
        }
        live_vanguards = {str(unit.id): unit for unit in turn.vanguards}
        live_rangers = {str(unit.id): unit for unit in turn.rangers}

        if active:
            core_id = self.memory.local_core_sortie_core_id
            sighting = self.memory.enemy_sightings.get(core_id or "")
            visible_core = visible_cores.get(core_id or "")
            elapsed = turn.tick - self.memory.local_core_sortie_started_tick
            if (
                sighting is None
                or not sighting.is_core
                or elapsed > BEACON_LOCAL_CORE_SORTIE_MAX_TICKS
            ):
                cancel("target_lost")
                return None, set(), set()
            if visible_core is not None and visible_core.state is not CoreState.NORMAL:
                cancel("target_moving")
                return None, set(), set()

            target = visible_core.position if visible_core is not None else sighting.position
            vanguard_ids = set(self.memory.local_core_sortie_vanguard_ids)
            ranger_ids = set(self.memory.local_core_sortie_ranger_ids)
            vanguard_units = [live_vanguards.get(unit_id) for unit_id in vanguard_ids]
            ranger_units = [live_rangers.get(unit_id) for unit_id in ranger_ids]
            if (
                len(vanguard_ids) != BEACON_LOCAL_CORE_SORTIE_VANGUARDS
                or len(ranger_ids) != BEACON_LOCAL_CORE_SORTIE_RANGERS
                or any(unit is None for unit in vanguard_units + ranger_units)
            ):
                cancel("squad_lost")
                return None, set(), set()
            if any(
                unit.hp < MAX_HP[unit.unit_type]
                for unit in vanguard_units + ranger_units
                if unit is not None
            ):
                cancel("squad_damaged")
                return None, set(), set()

            combat_enemies = [
                enemy
                for enemy in turn.visible_enemies
                if isinstance(enemy, UnitView)
                and enemy.unit_type in {UnitType.VANGUARD, UnitType.RANGER}
            ]
            sortie_units = [
                unit for unit in vanguard_units + ranger_units if unit is not None
            ]
            if any(
                _distance(enemy.position, target)
                <= BEACON_LOCAL_CORE_SORTIE_GUARD_RADIUS
                or any(
                    _distance(enemy.position, unit.position)
                    <= BEACON_EXPEDITION_LOCAL_THREAT_RADIUS
                    for unit in sortie_units
                )
                for enemy in combat_enemies
            ):
                cancel("combat_screen")
                return None, set(), set()

            self.memory.local_core_sortie_position = target
            return (
                target,
                {unit.id for unit in vanguard_units if unit is not None},
                {unit.id for unit in ranger_units if unit is not None},
            )

        if (
            len(home_vanguards)
            < BEACON_LOCAL_CORE_HOME_VANGUARDS
            + BEACON_LOCAL_CORE_SORTIE_VANGUARDS
            or len(home_rangers)
            < BEACON_LOCAL_CORE_HOME_RANGERS
            + BEACON_LOCAL_CORE_SORTIE_RANGERS
        ):
            return None, set(), set()

        candidates: list[tuple[str, EnemySighting, CoreView | None]] = []
        for core_id, sighting in self.memory.enemy_sightings.items():
            if not sighting.is_core:
                continue
            visible_core = visible_cores.get(core_id)
            if visible_core is not None and visible_core.state is not CoreState.NORMAL:
                continue
            target = visible_core.position if visible_core is not None else sighting.position
            if (
                turn.tick - sighting.seen_tick
                > BEACON_LOCAL_CORE_SORTIE_SIGHTING_MAX_AGE
                or _distance(turn.core.position, target)
                > BEACON_LOCAL_CORE_SORTIE_MAX_DISTANCE
                or any(
                    isinstance(enemy, UnitView)
                    and enemy.unit_type in {UnitType.VANGUARD, UnitType.RANGER}
                    and _distance(enemy.position, target)
                    <= BEACON_LOCAL_CORE_SORTIE_GUARD_RADIUS
                    for enemy in turn.visible_enemies
                )
            ):
                continue
            candidates.append((core_id, sighting, visible_core))
        if not candidates:
            return None, set(), set()

        core_id, sighting, visible_core = min(
            candidates,
            key=lambda item: (
                0 if item[2] is not None else 1,
                turn.tick - item[1].seen_tick,
                _distance(
                    turn.core.position,
                    item[2].position if item[2] is not None else item[1].position,
                ),
                item[0],
            ),
        )
        target = visible_core.position if visible_core is not None else sighting.position
        vanguard_candidates = sorted(
            (
                unit
                for unit in turn.vanguards
                if unit.id in home_vanguards
                and unit.hp >= MAX_HP[UnitType.VANGUARD]
            ),
            key=lambda unit: (_distance(unit.position, target), unit.id.bytes),
        )
        ranger_candidates = sorted(
            (
                unit
                for unit in turn.rangers
                if unit.id in home_rangers and unit.hp >= MAX_HP[UnitType.RANGER]
            ),
            key=lambda unit: (_distance(unit.position, target), unit.id.bytes),
        )
        if (
            len(vanguard_candidates) < BEACON_LOCAL_CORE_SORTIE_VANGUARDS
            or len(ranger_candidates) < BEACON_LOCAL_CORE_SORTIE_RANGERS
        ):
            return None, set(), set()

        sortie_vanguards = {
            unit.id
            for unit in vanguard_candidates[:BEACON_LOCAL_CORE_SORTIE_VANGUARDS]
        }
        sortie_rangers = {
            unit.id
            for unit in ranger_candidates[:BEACON_LOCAL_CORE_SORTIE_RANGERS]
        }
        self.memory.local_core_sortie_core_id = core_id
        self.memory.local_core_sortie_position = target
        self.memory.local_core_sortie_started_tick = turn.tick
        self.memory.local_core_sortie_vanguard_ids = {
            str(unit_id) for unit_id in sortie_vanguards
        }
        self.memory.local_core_sortie_ranger_ids = {
            str(unit_id) for unit_id in sortie_rangers
        }
        decisions.append(
            "local_core_sortie_started "
            f"target={target} core={core_id[:8]} "
            f"vanguards={len(sortie_vanguards)} rangers={len(sortie_rangers)}"
        )
        self.memory.decision_totals["local_core_sortie:started"] += 1
        return target, sortie_vanguards, sortie_rangers

    def _choose_beacon_local_core_sortie_vanguards(
        self,
        turn: Turn,
        planner: MovementPlanner,
        acted_units: set[UUID],
        decisions: list[str],
        target: Position,
        sortie_ids: set[UUID],
    ) -> None:
        core_id = self.memory.local_core_sortie_core_id
        visible_core = next(
            (
                enemy
                for enemy in turn.visible_enemies
                if isinstance(enemy, CoreView) and str(enemy.id) == core_id
            ),
            None,
        )
        for vanguard in sorted(turn.vanguards, key=_uuid_key):
            if vanguard.id not in sortie_ids or vanguard.id in acted_units:
                continue
            direction = (
                next(
                    (
                        direction
                        for direction in DIRECTION_ORDER
                        if _destination(vanguard.position, direction)
                        == visible_core.position
                    ),
                    None,
                )
                if visible_core is not None
                else None
            )
            if direction is not None:
                vanguard.sweep(direction)
                decisions.append(
                    f"vanguard:{_short_id(vanguard.id)} local_core_sweep "
                    f"target={target}"
                )
                self.memory.decision_totals["local_core_sortie:vanguard_sweep"] += 1
                continue
            if planner.toward(vanguard, target, "local_core_sortie_advance"):
                decisions.append(
                    f"vanguard:{_short_id(vanguard.id)} local_core_advance "
                    f"target={target}"
                )
                self.memory.decision_totals["local_core_sortie:vanguard_advance"] += 1

    def _choose_beacon_local_core_sortie_rangers(
        self,
        turn: Turn,
        planner: MovementPlanner,
        acted_units: set[UUID],
        decisions: list[str],
        target: Position,
        sortie_ids: set[UUID],
    ) -> None:
        core_id = self.memory.local_core_sortie_core_id
        visible_core = next(
            (
                enemy
                for enemy in turn.visible_enemies
                if isinstance(enemy, CoreView) and str(enemy.id) == core_id
            ),
            None,
        )
        for ranger in sorted(turn.rangers, key=_uuid_key):
            if ranger.id not in sortie_ids or ranger.id in acted_units:
                continue
            core_shots = [
                (enemy, cell)
                for enemy, cell in self._ranger_shot_candidates(turn, ranger, planner)
                if isinstance(enemy, CoreView) and str(enemy.id) == core_id
            ]
            if core_shots:
                enemy, cell = min(core_shots, key=lambda pair: pair[1])
                ranger.shoot(enemy, expected_cell=cell)
                self._mark_ranger_shot(enemy, cell)
                decisions.append(
                    f"ranger:{_short_id(ranger.id)} local_core_shoot "
                    f"target={target} expected={cell}"
                )
                self.memory.decision_totals["local_core_sortie:ranger_shoot"] += 1
                continue
            firing_position = (
                self._core_assault_ranger_position(ranger, target, planner)
                if visible_core is not None
                else None
            )
            destination = firing_position or target
            if planner.toward(ranger, destination, "local_core_sortie_firing"):
                decisions.append(
                    f"ranger:{_short_id(ranger.id)} local_core_advance "
                    f"target={target} firing={destination}"
                )
                self.memory.decision_totals["local_core_sortie:ranger_advance"] += 1

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
        """巡逻半径偏外环（内圈火力猛，Core 不深入）。"""
        inner_r, outer_r = self.memory.lightning_ring
        if outer_r <= inner_r:
            return outer_r
        return round(inner_r + (outer_r - inner_r) * LIGHTNING_PATROL_RADIUS_FRACTION)

    def _lightning_clamp_to_donut(self, position: Position) -> Position:
        """把一个点沿 max-norm 径向投到方环内（inner_r ≤ max(|x|,|y|) ≤ outer_r）。"""
        inner_r, outer_r = self.memory.lightning_ring
        x, y = position
        radius = max(abs(x), abs(y))
        if radius == 0:
            return (inner_r, 0)
        if radius < inner_r:
            scale = inner_r / radius
        elif radius > outer_r:
            scale = outer_r / radius
        else:
            return position
        return (round(x * scale), round(y * scale))

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

    def _lightning_patrol_waypoint(self, turn: Turn) -> Position:
        """Core 巡逻点：沿半径 pr 的方形周界四角轮转，遇敌绕开。

        越界（环内或环外）时最近角即目标——走到环上的路本身就算正常巡逻。
        到达死区后推进到下一角，形成绕环转圈。
        新增：检查目标象限是否有可见敌方战斗单位，有则跳过该角。
        """
        core = turn.core
        pr = self._lightning_patrol_radius()
        # 半径 pr 的方形周界四角（顺时针）。
        corners = ((pr, pr), (pr, -pr), (-pr, -pr), (-pr, pr))
        waypoint = self.memory.lightning_patrol_waypoint
        phase = self.memory.lightning_patrol_phase % 4

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

    def _lightning_claim_for(self, unit_id: str) -> str | None:
        """Return this unit's claimed enemy-core UUID, releasing stale claims."""
        core_id = self.memory.lightning_claims.get(unit_id)
        if core_id is None:
            return None
        sighting = self.memory.enemy_sightings.get(core_id)
        if sighting is None or not sighting.is_core:
            # 记录已失效（格确认空 / Core 被摧毁）→ 释放 claim。
            self.memory.lightning_claims.pop(unit_id, None)
            return None
        return core_id

    def _lightning_target_position(
        self,
        turn: Turn,
        core_id: str,
    ) -> tuple[Position, CoreView | None]:
        """Recorded coord, plus the live CoreView when the target is in vision."""
        visible_core = next(
            (
                enemy
                for enemy in turn.visible_enemies
                if isinstance(enemy, CoreView) and str(enemy.id) == core_id
            ),
            None,
        )
        if visible_core is not None:
            return visible_core.position, visible_core
        sighting = self.memory.enemy_sightings[core_id]
        return sighting.position, None

    def _lightning_target_attended(
        self,
        turn: Turn,
        target_position: Position,
    ) -> bool:
        """True only when a visible combat unit is close enough to hit the attacker.

        守卫在目标 CLOSE_RADIUS（3）内——真能 sweep/shoot 到进攻方——才算贴脸，
        此时绕不开，应释放 claim 撤退。更远（4-8）的守卫可能只是路过/视野边缘
        闪现，不释放；改由 _lightning_guard_cells 把它们当障碍绕开，从侧面包抄。
        这样避免"进一步看见远处守卫→释放→退一步看不见→又 claim→又进"的震荡。
        """
        return any(
            isinstance(enemy, UnitView)
            and enemy.unit_type in {UnitType.VANGUARD, UnitType.RANGER}
            and _distance(enemy.position, target_position)
            <= LIGHTNING_HUNT_GUARD_CLOSE_RADIUS
            for enemy in turn.visible_enemies
        )

    def _lightning_target_crowded(
        self,
        target_position: Position,
        *,
        max_age: int = LIGHTNING_CROWD_SIGHTING_MAX_AGE,
    ) -> bool:
        """目标周围是否近期 sighting 密集（含雾里看不见的守卫）。

        enemy_sightings 记录见过的敌方单位（is_core=False 的可能是工人也可能是
        战斗单位——dataclass 没存 unit_type，保守都算）。若目标 GUARD_RADIUS 内
        近期 sighting 数 ≥ CROWD_THRESHOLD，视为重兵把守的 Core，应放弃猎杀，
        不该硬凑。这补住"守卫在雾里看不见 → acquire 误判无护卫 → 凑过去卡死"
        的漏洞。
        """
        turn_tick = self.memory.last_tick
        nearby = 0
        for sighting in self.memory.enemy_sightings.values():
            if sighting.is_core:
                continue
            if turn_tick - sighting.seen_tick > max_age:
                continue
            if _distance(sighting.position, target_position) <= LIGHTNING_HUNT_GUARD_RADIUS:
                nearby += 1
        return nearby >= LIGHTNING_CROWD_THRESHOLD

    def _lightning_guard_cells(
        self,
        turn: Turn,
        target_position: Position,
    ) -> set[Position]:
        """可见的敌方战斗单位及其四邻格，作为寻路 avoid 集合让猎手绕开守卫。

        守卫本体 + 四邻（先锋 sweep 打相邻格，游侠 shoot 打 1-3 射线），把这片
        当障碍，planner.toward 自然选别的路从侧面包抄，而不是正对守卫进退。
        只覆盖目标附近的守卫（≤ LIGHTNING_HUNT_GUARD_RADIUS），远处的不相关。
        """
        cells: set[Position] = set()
        for enemy in turn.visible_enemies:
            if not (
                isinstance(enemy, UnitView)
                and enemy.unit_type in {UnitType.VANGUARD, UnitType.RANGER}
            ):
                continue
            if _distance(enemy.position, target_position) > LIGHTNING_HUNT_GUARD_RADIUS:
                continue
            cells.add(enemy.position)
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                cells.add((enemy.position[0] + dx, enemy.position[1] + dy))
        return cells

    def _lightning_blacklist_core(self, core_id: str) -> None:
        """把一个敌方 Core 永久拉黑:释放所有 claim 它的单位(集火编队同步撤退),
        从 sightings 清脏数据。供"判为重兵把守"时统一调用。
        """
        self.memory.lightning_blacklist.add(core_id)
        owners = [
            uid
            for uid, cid in self.memory.lightning_claims.items()
            if cid == core_id
        ]
        for uid in owners:
            self.memory.lightning_claims.pop(uid, None)
        self.memory.enemy_sightings.pop(core_id, None)

    def _lightning_acquire_target(
        self,
        turn: Turn,
        unit: Unit,
    ) -> str | None:
        """Claim the nearest recorded unguarded enemy core, forming focus fire.

        不排除被其他 unit claim 的目标——同一 Core 允许多 unit 集火,自然汇聚。
        防全员扑一个致 Core 失防:某 Core 已被 ≥ LIGHTNING_FOCUS_MAX_ATTACKERS 个
        unit claim 时,跳过它选下一个合格目标。
        """
        claim_counts: dict[str, int] = {}
        for core_id in self.memory.lightning_claims.values():
            claim_counts[core_id] = claim_counts.get(core_id, 0) + 1
        candidates: list[tuple[str, EnemySighting]] = []
        # 遍历快照:主体内可能因 _lightning_blacklist_core 删 sightings 改字典大小。
        for core_id, sighting in list(self.memory.enemy_sightings.items()):
            if not sighting.is_core:
                continue
            if core_id in self.memory.lightning_blacklist:
                # 永久放弃过的重兵 Core，不再回头;顺手从 sightings 清脏。
                self.memory.enemy_sightings.pop(core_id, None)
                continue
            # 距离检查：只追击 LIGHTNING_HUNT_MAX_DISTANCE 内的目标，防全员被吸走太远。
            if turn.core is not None:
                dist = _distance(sighting.position, turn.core.position)
                if dist > LIGHTNING_HUNT_MAX_DISTANCE:
                    continue
            if claim_counts.get(core_id, 0) >= LIGHTNING_FOCUS_MAX_ATTACKERS:
                # 已集火满员 → 跳过选下一个,防 Core 失防。
                continue
            if self._lightning_target_attended(turn, sighting.position):
                # 贴脸有守卫 → 黑名单，永久跳过;同步清 sightings 脏数据。
                self._lightning_blacklist_core(core_id)
                continue
            if self._lightning_target_crowded(sighting.position):
                # 雾里也围着守卫的重兵 Core → 黑名单，永久放弃;清脏。
                self._lightning_blacklist_core(core_id)
                continue
            candidates.append((core_id, sighting))
        if not candidates:
            return None
        core_id, sighting = min(
            candidates,
            key=lambda pair: (
                _distance(unit.position, pair[1].position),
                pair[0],
            ),
        )
        self.memory.lightning_claims[str(unit.id)] = core_id
        return core_id

    def _lightning_defense_tier(self, turn: Turn) -> str:
        """按 visible 敌方战斗单位到我 Core 最近距离分档:NEAR|MID|FAR|NONE。

        替换原 _core_emergency_threats 单层判断,实现"反应强度按敌方深入系统深度而定":
          NEAR(≤6)  → 全员含工人回防线卡位肉盾(沿用 _core_emergency 阈值)。
          MID(≤20)  → 全体游侠回防,工人继续经济或就地卡位。
          FAR(≤40)  → 仅那个方位附近游侠游击警告,不全撤;余者照常绕圈。
          NONE      → 无人侵,正常绕轨道。
        _core_recently_damaged 兜底强制 NEAR(已受伤不等再判距)。
        """
        if turn.core is None:
            return "NONE"
        if self._core_recently_damaged(turn):
            return "NEAR"
        d_min: int | None = None
        for enemy in turn.visible_enemies:
            if not (
                isinstance(enemy, UnitView)
                and enemy.unit_type in {UnitType.VANGUARD, UnitType.RANGER}
            ):
                continue
            d = _distance(enemy.position, turn.core.position)
            if d_min is None or d < d_min:
                d_min = d
        if d_min is None:
            return "NONE"
        if d_min <= LIGHTNING_DEFENSE_RING_NEAR:
            return "NEAR"
        if d_min <= LIGHTNING_DEFENSE_RING_MID:
            return "MID"
        if d_min <= LIGHTNING_DEFENSE_RING_FAR:
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

    def _lightning_breakthrough_threat_check(
        self,
        turn: Turn,
        ranger: Unit,
    ) -> tuple[str, Position | None]:
        """开路游侠威胁检测：返回 (action, target)。

        用户战术要求（开路轨道职责）：
        - 只有 1v1 先锋时游击（利用射程优势）
        - 见游侠/多敌立即绕路（逃向 Core）

        action:
            "flee"   - 发现敌方游侠或多敌，逃向 Core
            "kite"   - 发现单个敌方先锋，保持 2-3 格游击
            "patrol" - 无威胁，继续巡逻

        target: 逃跑/游击目标位置，None 表示继续巡逻
        """
        nearby_enemies = []
        for enemy in turn.visible_enemies:
            if not isinstance(enemy, UnitView):
                continue
            if enemy.unit_type not in {UnitType.VANGUARD, UnitType.RANGER}:
                continue
            dist = _distance(ranger.position, enemy.position)
            if dist <= LIGHTNING_LOCAL_THREAT_RADIUS:
                nearby_enemies.append((enemy, dist))

        if not nearby_enemies:
            return ("patrol", None)

        # 发现敌方游侠 → 立即逃跑（我方 2HP 易亏，射程对等无优势）
        if any(e.unit_type is UnitType.RANGER for e, _ in nearby_enemies):
            return ("flee", turn.core.position if turn.core else None)

        # 多个敌人 → 逃跑（敌众我寡）
        if len(nearby_enemies) > 1:
            return ("flee", turn.core.position if turn.core else None)

        # 单个先锋 → 游击（射程 1-3 优势，先锋近战需贴脸）
        enemy, dist = nearby_enemies[0]
        if enemy.unit_type is UnitType.VANGUARD:
            kite_pos = self._lightning_kiting_position(turn, ranger, enemy)
            if kite_pos != ranger.position:
                return ("kite", kite_pos)
            # 距离已合适（2-3），原地不动让主逻辑处理射击
            return ("patrol", None)

        return ("patrol", None)

    def _lightning_engage_assessment(
        self,
        turn: Turn,
        target_position: Position,
    ) -> str:
        """判定对该敌方 Core 是否进攻及打法:CHICKEN|PRESS|SKIP。

        兵种细分(用户原则:非必要不进攻,游侠 2HP 易亏):
          CHICKEN — 无任何战斗单位护卫 → 直接打(沿用现状)。
          PRESS   — 只有先锋(近战贴脸 1 格),我游侠手长 1-3 射程优势 → 主动游击:
                    一个游侠贴射程内勾引、其余在射程内狙击,可无伤取胜。
          SKIP    — 有游侠(远程 1-3)→ 我 2HP 易亏 → 回避:不 claim,对该 Core 拉黑。
        "无护卫"判定复用现有 _lightning_target_attended(贴脸≤3)与 _lightning_target_crowded
        (雾里围满);两者皆否才进兵种细分。
        """
        if self._lightning_target_attended(turn, target_position):
            # 贴脸已有守卫——上游应已 blacklist;保守判 SKIP 退。
            return "SKIP"
        if self._lightning_target_crowded(target_position):
            return "SKIP"
        has_vanguard = False
        has_ranger = False
        for enemy in turn.visible_enemies:
            if not isinstance(enemy, UnitView):
                continue
            if _distance(enemy.position, target_position) > LIGHTNING_HUNT_GUARD_RADIUS:
                continue
            if enemy.unit_type is UnitType.VANGUARD:
                has_vanguard = True
            elif enemy.unit_type is UnitType.RANGER:
                has_ranger = True
        if has_ranger:
            return "SKIP"
        if has_vanguard:
            return "PRESS"
        return "CHICKEN"

    def _lightning_find_nearby_unguarded_core(
        self,
        turn: Turn,
        ranger: Unit,
    ) -> CoreView | None:
        """开路游侠巡逻途中搜索附近无守卫/弱守卫的敌方 Core（选择性交战用）。

        仅搜索可见的 CoreView（不使用 enemy_sightings 历史记录），确保信息新鲜。
        返回距离游侠最近的、满足"无守卫或仅1先锋"的敌方 Core。
        """
        candidates = []
        for enemy in turn.visible_enemies:
            if not isinstance(enemy, CoreView):
                continue
            # 跳过已拉黑的 Core
            if str(enemy.id) in self.memory.lightning_blacklist:
                continue
            # 检测守卫情况
            if self._lightning_target_attended(turn, enemy.position):
                continue
            if self._lightning_target_crowded(enemy.position):
                continue
            # 统计守卫兵种
            guard_vanguards = 0
            guard_rangers = 0
            for other_enemy in turn.visible_enemies:
                if not isinstance(other_enemy, UnitView):
                    continue
                dist = _distance(other_enemy.position, enemy.position)
                if dist > LIGHTNING_HUNT_GUARD_RADIUS:
                    continue
                if other_enemy.unit_type is UnitType.VANGUARD:
                    guard_vanguards += 1
                elif other_enemy.unit_type is UnitType.RANGER:
                    guard_rangers += 1
            # 有游侠守卫 → 跳过（绕路）
            if guard_rangers > 0:
                continue
            # 无守卫 or 仅先锋守卫 → 候选
            dist_to_ranger = _distance(ranger.position, enemy.position)
            candidates.append((dist_to_ranger, enemy))

        if not candidates:
            return None
        candidates.sort(key=lambda x: x[0])
        return candidates[0][1]

    def _lightning_should_breakthrough_engage(
        self,
        turn: Turn,
        ranger: Unit,
        target_core: CoreView,
    ) -> bool:
        """判定开路游侠是否应对该 Core 交战（选择性交战规则）。

        交战条件：
        1. 无守卫 → 打
        2. 仅1先锋守卫 → 游击（利用射程优势）
        3. 有游侠守卫 OR 敌方战斗单位数量 > 我方开路游侠数量 → 绕路

        返回 True 表示应交战，False 表示绕路继续巡逻。
        """
        # 统计目标周围的敌方战斗单位
        enemy_combat_units = []
        for enemy in turn.visible_enemies:
            if not isinstance(enemy, UnitView):
                continue
            if enemy.unit_type not in {UnitType.VANGUARD, UnitType.RANGER}:
                continue
            dist = _distance(enemy.position, target_core.position)
            if dist <= LIGHTNING_HUNT_GUARD_RADIUS:
                enemy_combat_units.append(enemy)

        # 有游侠守卫 → 绕路（不打）
        for enemy_unit in enemy_combat_units:
            if enemy_unit.unit_type is UnitType.RANGER:
                return False

        # 统计我方开路游侠数量
        my_breakthrough_rangers = 0
        ordered_rangers = sorted(turn.rangers, key=_uuid_key)
        for index, r in enumerate(ordered_rangers):
            if index < LIGHTNING_BREAKTHROUGH_SLOT_COUNT:
                my_breakthrough_rangers += 1

        # 敌方战斗单位数量 > 我方开路游侠数量 → 绕路（敌众我寡）
        if len(enemy_combat_units) > my_breakthrough_rangers:
            return False

        # 否则：无守卫 or 仅先锋守卫 → 可以打
        return True

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

    def _lightning_sector_target(
        self,
        turn: Turn,
        unit: Unit,
    ) -> Position | None:
        """无猎杀目标时，单位沿方环周界探索（不出环），拓展视野。

        以单位当前位置投影到方环周界为锚，再沿周界顺时针小步偏移。这样单位
        永远在"自己附近"的环上走，不会穿越原点跑到对面象限（旧 bug：固定四角
        把第四象限的单位派去第一象限）。每单位偏移相位不同以分散覆盖。
        """
        pr = self._lightning_patrol_radius()
        anchor = self._lightning_clamp_to_donut(unit.position)
        # 沿周界顺时针推进：周界点的主轴分量饱和（±pr），次轴未饱和；
        # 把次轴向饱和方向推一格即"沿周界走一圈"。方向由单位位置象限决定。
        ordered = sorted(
            (u for u in (*turn.vanguards, *turn.rangers)),
            key=_uuid_key,
        )
        try:
            index = ordered.index(unit)
        except ValueError:
            index = 0
        # 每单位一个相位偏移（tick//16 让它随时间推进，单位 index 分散覆盖）。
        phase = (turn.tick // 16 + index) % len(LIGHTNING_PATROL_COMPASS)
        dx, dy = LIGHTNING_PATROL_COMPASS[phase]
        step = max(1, LIGHTNING_SECTOR_STEP // 2)
        target = (anchor[0] + dx * step, anchor[1] + dy * step)
        target = self._lightning_clamp_to_donut(target)
        # 巡逻半径对齐：把目标 max-norm 拉到 pr，让它沿周界而非环内游荡。
        tx, ty = target
        t_radius = max(abs(tx), abs(ty))
        if 0 < t_radius and t_radius != pr:
            scale = pr / t_radius
            target = self._lightning_clamp_to_donut(
                (round(tx * scale), round(ty * scale))
            )
        if target == unit.position:
            return None
        return target

    def _lightning_core_heading_vector(self, turn: Turn) -> tuple[int, int]:
        """Core 预定行进单位向量(从当前巡逻 waypoint 推出,归一化)。

        waypoint - core.position 取主轴方向归一化为单位向量;若 Core 已到角死区
        (差很小)用当前 phase 的下一角;再不行用 memory.core_heading;最后 UP 兜底。
        返回 (dx, dy),至少一个分量为 ±1。用于游侠/先锋算"前方"与"正交"铺视野。
        """
        core = turn.core
        if core is None:
            return (0, 1)
        waypoint = self._lightning_patrol_waypoint(turn)
        dx = waypoint[0] - core.position[0]
        dy = waypoint[1] - core.position[1]
        # 死区(已到角):用下一角避免方向归零。
        if abs(dx) <= CORE_BEACON_HYSTERESIS and abs(dy) <= CORE_BEACON_HYSTERESIS:
            pr = self._lightning_patrol_radius()
            corners = ((pr, pr), (pr, -pr), (-pr, -pr), (-pr, pr))
            nxt = (self.memory.lightning_patrol_phase + 1) % 4
            dx = corners[nxt][0] - core.position[0]
            dy = corners[nxt][1] - core.position[1]
        if dx == 0 and dy == 0:
            heading = self.memory.core_heading
            if heading is not None:
                return (heading.delta[0], heading.delta[1])
            return (0, 1)
        # 归一化为单位向量(保留两轴对角方向):每轴 ±1 或 0。
        sx = (dx > 0) - (dx < 0)
        sy = (dy > 0) - (dy < 0)
        return (sx, sy)

    def _lightning_ranger_scout_target(
        self,
        turn: Turn,
        ranger: Unit,
    ) -> Position | None:
        """游侠独立绕圈探路:每游侠一条同心方环 lane,沿周界四角同向转圈。

        关键:游侠绕圈**与 Core 位置解耦**,沿自己的 lane 独立推进——Core 1格/4tick
        太慢,游侠 1格/tick,若锚在 Core 前 LEAD 格会被迫等 Core。这里改为每个游侠
        认领一条同心方环(lane 决定径向半径偏移),沿周界四角同向转圈,到达角死区后
        推进下一角。多游侠沿各自 lane 同向绕圈,共同覆盖 Core 轨道所在的环面。

        lane 间距 = LIGHTNING_SCOUT_LANE_GAP,径向铺开多条同心周界(视野不重叠);
        所有游侠与 Core 同方向(都用周界顺时针角序),故游侠轨道必覆盖 Core 轨道。
        钳到方环内防越框。
        """
        core = turn.core
        if core is None:
            return None
        # 按 UUID 序给固定 lane index(径向同心周界的偏移档位)。
        ordered = sorted(turn.rangers, key=_uuid_key)
        uid = str(ranger.id)
        if uid not in self.memory.lightning_scout_lanes:
            self.memory.lightning_scout_lanes[uid] = len(ordered)
        live = {str(r.id) for r in turn.rangers}
        for dead in [k for k in self.memory.lightning_scout_lanes if k not in live]:
            self.memory.lightning_scout_lanes.pop(dead, None)
            self.memory.lightning_scout_phase.pop(dead, None)
        live_lanes = sorted(
            self.memory.lightning_scout_lanes.items(), key=lambda kv: kv[1]
        )
        compact = {uid_: i for i, (uid_, _) in enumerate(live_lanes)}
        self.memory.lightning_scout_lanes = compact
        lane = compact.get(uid, 0)
        n = max(1, len(compact))
        # 同心周界半径:以 pr 为中轴,llane 对称偏移,每档 LANE_GAP 格径向距离。
        pr = self._lightning_patrol_radius()
        half = (n - 1) / 2
        radius = pr + int(round((lane - half) * LIGHTNING_SCOUT_LANE_GAP))
        inner_r, outer_r = self.memory.lightning_ring
        radius = max(inner_r + 2, min(outer_r - 2, radius))  # 钳在环内留余量
        # 该游侠的方环四角(顺时针,与 Core 巡逻同序)。
        corners = (
            (radius, radius),
            (radius, -radius),
            (-radius, -radius),
            (-radius, radius),
        )
        phase = self.memory.lightning_scout_phase.get(uid, 0) % 4
        # 首次:与 Core 巡逻 phase 对齐(朝 Core 前方的角),而非"最近角"。
        # 否则游侠会跑向 Core 身后/相反方向的角,浪费并把视野铺在 Core 已走过的后方。
        # 对齐后游侠顺方向绕圈,顺便点亮 Core 前方视野。
        if uid not in self.memory.lightning_scout_phase:
            phase = self.memory.lightning_patrol_phase % 4
            self.memory.lightning_scout_phase[uid] = phase
        target = corners[phase]
        # 动态跳角:目标角尚远却已知埋在乱石堆里 → 提前推下一角绕行。
        if (
            _distance(ranger.position, target) > CORE_BEACON_HYSTERESIS * 2
            and self._lightning_corner_obstructed(target)
        ):
            phase = (phase + 1) % 4
            self.memory.lightning_scout_phase[uid] = phase
            target = corners[phase]
        # 到达当前角死区 → 推进下一角(独立绕圈,不等 Core)。
        if _distance(ranger.position, target) <= CORE_BEACON_HYSTERESIS:
            phase = (phase + 1) % 4
            self.memory.lightning_scout_phase[uid] = phase
            target = corners[phase]
        target = self._lightning_clamp_to_donut(target)
        if target == ranger.position:
            return None
        return target

    def _lightning_breakthrough_target(
        self,
        turn: Turn,
        ranger: Unit,
        lane_idx: int,
    ) -> Position | None:
        """开路轨道(恒星维度,绕原点外大同心方环)下一目标点。

        与 _lightning_ranger_scout_target 共享"绕原点四角顺时针 + phase 独立推进"
        机制,但半径档更大——在 Core 轨道 pr 之外 LIGHTNING_BREAKTHROUGH_RING_OFFSET
        再外括,且 4 个开路游侠按 lane_idx 错开同心环(每档一个游侠视野半径)。
        越外越钳到 outer_r(绝不深入 <inner 的火力密集区)。

        开路游侠自己转自己的、不等 Core(游侠 1格/tick,Core 1格/4tick):开路可能
        已绕原点好几圈,Core 才转一圈。在恒星轨道维度提前点亮覆盖 Core 轨道的资源、
        摧毁低守卫敌方 Core。仅勤王(NEAR/MID 回防)时回援,否则持续绕圈开路。
        """
        core = turn.core
        if core is None:
            return None
        uid = str(ranger.id)
        pr = self._lightning_patrol_radius()
        gap_r = LIGHTNING_ORBIT_LANE_GAP_RADIUS[UnitType.RANGER]
        radius = pr + LIGHTNING_BREAKTHROUGH_RING_OFFSET + lane_idx * gap_r
        inner_r, outer_r = self.memory.lightning_ring
        radius = max(inner_r + 2, min(outer_r, radius))  # 不深入内圈
        corners = (
            (radius, radius),
            (radius, -radius),
            (-radius, -radius),
            (-radius, radius),
        )
        phase = self.memory.lightning_breakthrough_phase.get(uid, 0) % 4
        if uid not in self.memory.lightning_breakthrough_phase:
            phase = self.memory.lightning_patrol_phase % 4
            self.memory.lightning_breakthrough_phase[uid] = phase
        target = corners[phase]
        # 动态跳角:目标角尚远却已知埋在乱石堆里 → 提前推下一角绕行。
        if (
            _distance(ranger.position, target) > CORE_BEACON_HYSTERESIS * 2
            and self._lightning_corner_obstructed(target)
        ):
            phase = (phase + 1) % 4
            self.memory.lightning_breakthrough_phase[uid] = phase
            target = corners[phase]
        if _distance(ranger.position, target) <= CORE_BEACON_HYSTERESIS:
            phase = (phase + 1) % 4
            self.memory.lightning_breakthrough_phase[uid] = phase
            target = corners[phase]
        # 新增：目标区域访问饱和时强制跳角（提前放弃不可达目标）
        elif (
            _distance(ranger.position, target) < CORE_BEACON_HYSTERESIS * 2
            and self.memory.visited.get(target, 0) > 10
            and self._lightning_corner_obstructed(target)
        ):
            phase = (phase + 1) % 4
            self.memory.lightning_breakthrough_phase[uid] = phase
            target = corners[phase]
        target = self._lightning_clamp_to_donut(target)
        if target == ranger.position:
            return None
        return target

    def _lightning_calculate_outer_first_orbits(
        self,
        unit_count: int,
        vision_radius: int,
        gap: int,
        inner_radius: int,
        min_units_per_orbit: int = 2,
    ) -> list[tuple[int, int]]:
        """混合策略：先铺开领土（每轨道最少单位），再按周长比例加密。

        返回 [(radius, unit_count), ...] 列表。

        策略：
        1. Phase 1: 每条轨道先分配 min_units_per_orbit，尽量铺开
        2. Phase 2: 剩余单位按周长比例分配（外层轨道周长大，分配更多）
        3. 单轨道上限8个单位（四角+四边中点）
        """
        if unit_count == 0:
            return []

        # 最多能铺几条轨道
        max_orbits = unit_count // min_units_per_orbit

        # 合理外边界：游侠≤80，工人≤60
        max_radius_by_gap = {5: 80, 3: 60}
        reasonable_limit = max_radius_by_gap.get(gap, 100)
        max_radius = min(inner_radius + gap * max_orbits, reasonable_limit)

        result = []
        remaining = unit_count
        radius = inner_radius

        # Phase 1: 每条轨道先分配最少单位，铺开领土
        while remaining >= min_units_per_orbit and radius <= max_radius:
            result.append([radius, min_units_per_orbit])
            remaining -= min_units_per_orbit
            radius += gap

        if remaining == 0:
            return [(r, c) for r, c in result]

        # Phase 2: 剩余单位按周长比例分配
        # 计算各轨道周长
        circumferences = [8 * r for r, c in result]
        total_circumference = sum(circumferences)

        if total_circumference > 0:
            # 按周长比例分配剩余单位
            for i, circ in enumerate(circumferences):
                if remaining <= 0:
                    break
                r, count = result[i]
                # 该轨道应分配的额外单位数 = 剩余单位 × (该轨道周长 / 总周长)
                # 四舍五入，至少1个（如果总剩余>0）
                extra = max(1, round(remaining * circ / total_circumference))
                # 不超过单轨道上限
                max_extra = min(extra, 8 - count, remaining)
                result[i][1] = count + max_extra
                remaining -= max_extra

        # Phase 3: 如果Phase 2舍入导致还有剩余，从外向内依次加1
        orbit_idx = len(result) - 1
        while remaining > 0 and orbit_idx >= 0:
            r, count = result[orbit_idx]
            if count < 8:
                result[orbit_idx][1] = count + 1
                remaining -= 1
            orbit_idx -= 1

        return [(r, c) for r, c in result]

    def _lightning_assign_orbit_lanes(
        self,
        turn: Turn,
        role: UnitType,
    ) -> dict[str, tuple[int, int]]:
        """给某 role 的所有存活单位分配 (radius, group_index)。

        外圈优先混合策略：先铺开外层轨道（最大化领土），再按周长比例加密。
        同一半径的单位通过 group_index 错开 phase（phase_offset = group_index * 4 // group_size）。

        返回 {uid: (radius, group_index)}。缓存到 memory.lightning_orbit_lanes[role.value]。
        """
        role_key = role.value
        if role is UnitType.VANGUARD:
            units = list(turn.vanguards)
        elif role is UnitType.RANGER:
            units = list(turn.rangers)
        else:
            units = list(turn.workers)

        live = {str(u.id) for u in units}
        stored = dict(self.memory.lightning_orbit_lanes.get(role_key, {}))

        # 清理死亡单位
        for dead in [k for k in stored if k not in live]:
            stored.pop(dead, None)
            self.memory.lightning_orbit_phase.pop(dead, None)

        # 计算轨道分配
        gap = LIGHTNING_ORBIT_LANE_GAP_RADIUS[role]
        if role is UnitType.VANGUARD:
            inner_radius = LIGHTNING_NEAR_ORBIT_RADIUS
        elif role is UnitType.RANGER:
            vg_count = len(turn.vanguards)
            gap_v = LIGHTNING_ORBIT_LANE_GAP_RADIUS[UnitType.VANGUARD]
            vg_outer = LIGHTNING_NEAR_ORBIT_RADIUS + max(0, vg_count - 1) * gap_v
            inner_radius = vg_outer + gap
        else:  # WORKER
            vg_count = len(turn.vanguards)
            rk_count = len(turn.rangers)
            gap_v = LIGHTNING_ORBIT_LANE_GAP_RADIUS[UnitType.VANGUARD]
            gap_r = LIGHTNING_ORBIT_LANE_GAP_RADIUS[UnitType.RANGER]
            vg_outer = LIGHTNING_NEAR_ORBIT_RADIUS + max(0, vg_count - 1) * gap_v
            rk_inner = vg_outer + gap_r
            rk_outer = rk_inner + max(0, rk_count - 1) * gap_r
            inner_radius = rk_outer + 3

        vision_radius = LIGHTNING_ORBIT_LANE_GAP_RADIUS[role]
        orbit_distribution = self._lightning_calculate_outer_first_orbits(
            len(units), vision_radius, gap, inner_radius, min_units_per_orbit=2
        )

        # 按UUID序分配单位到各半径
        sorted_units = sorted(units, key=_uuid_key)
        assignments = {}
        unit_idx = 0

        for radius, count in orbit_distribution:
            for group_idx in range(count):
                if unit_idx >= len(sorted_units):
                    break
                uid = str(sorted_units[unit_idx].id)
                assignments[uid] = (radius, group_idx)
                unit_idx += 1

        self.memory.lightning_orbit_lanes[role_key] = assignments
        return assignments

    def _lightning_orbit_waypoint(
        self,
        turn: Turn,
        unit: Unit,
        role: UnitType,
        lane: int | None = None,
    ) -> Position | None:
        """绕 Core 转的行星轨道下一目标点。外圈优先分配，同半径单位phase错开。

        圆心 = core.position。半径由 _lightning_assign_orbit_lanes 分配。
        同一半径的多个单位通过 phase_offset 错开（0/1/2/3 对应四个角）。
        """
        core = turn.core
        if core is None:
            return None

        uid = str(unit.id)

        # 重新分配（顺带剪枝死亡单位）
        lanes = self._lightning_assign_orbit_lanes(turn, role)

        if uid not in lanes:
            return None

        radius, group_index = lanes[uid]

        # 计算该半径上的总单位数（用于phase_offset）
        units_at_radius = sum(1 for (r, _) in lanes.values() if r == radius)
        phase_offset = (group_index * 4) // max(1, units_at_radius)

        # 生成四角目标
        cx, cy = core.position
        corners = (
            (cx + radius, cy + radius),
            (cx + radius, cy - radius),
            (cx - radius, cy - radius),
            (cx - radius, cy + radius),
        )

        # 读取/初始化 base_phase
        base_phase = self.memory.lightning_orbit_phase.get(uid)
        if base_phase is None:
            base_phase = self.memory.lightning_patrol_phase % 4
            self.memory.lightning_orbit_phase[uid] = base_phase

        target = corners[(base_phase + phase_offset) % 4]

        # 动态跳角:目标角尚远却已知埋在乱石堆里 → 提前推下一角绕行。
        if (
            _distance(unit.position, target) > CORE_BEACON_HYSTERESIS * 2
            and self._lightning_corner_obstructed(target)
        ):
            base_phase = (base_phase + 1) % 4
            self.memory.lightning_orbit_phase[uid] = base_phase
            target = corners[(base_phase + phase_offset) % 4]

        if _distance(unit.position, target) <= CORE_BEACON_HYSTERESIS:
            base_phase = (base_phase + 1) % 4
            self.memory.lightning_orbit_phase[uid] = base_phase
            target = corners[(base_phase + phase_offset) % 4]

        # 新增：目标区域访问饱和时强制跳角（提前放弃不可达目标）
        elif (
            _distance(unit.position, target) < CORE_BEACON_HYSTERESIS * 2
            and self.memory.visited.get(target, 0) > 10
            and self._lightning_corner_obstructed(target)
        ):
            base_phase = (base_phase + 1) % 4
            self.memory.lightning_orbit_phase[uid] = base_phase
            target = corners[(base_phase + phase_offset) % 4]

        target = self._lightning_clamp_to_donut(target)

        if target == unit.position:
            return None

        return target

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
                score = (
                    -exits * LIGHTNING_ESCAPE_EXIT_WEIGHT
                    + visited_penalty
                    + planner.threat.get(destination, 0) * 4.0
                    + heading_penalty
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

    def _lightning_vanguard_vee_target(
        self,
        turn: Turn,
        vanguard: Unit,
    ) -> Position | None:
        """先锋 V 字纵深猎杀的下一目标点(出探机状态机)。

        OUTBOUND:从 origin 沿 Core 行进方向的正交方向深入 LIGHTNING_VEE_DEPTH 格
        (leg 0 正向、leg 1 反向,两腿成 V);到达(距目标 ≤ REACH_TOLERANCE)翻 INBOUND。
        INBOUND:朝当前 Core 位置走;到达 ≤ HOME_TOLERANCE 翻 OUTBOUND,leg 翻转,
        origin 重设为当前 Core 位置(Core 已前移,下一轮扫新地带)。
        目标均钳到方环内防越框;翻 INBOUND 的"Core 受威胁"触发由上游 _choose_vanguards_recall
        强召回处理,这里只管"周期返"。
        """
        core = turn.core
        if core is None:
            return None
        uid = str(vanguard.id)
        state = self.memory.lightning_vee_state.get(uid)
        fwd = self._lightning_core_heading_vector(turn)
        perp = (-fwd[1], fwd[0])
        if state is None:
            # 首次:OUTBOUND,以当前位置为 origin,目标正交方向纵深 D。
            origin = vanguard.position
            sign = 1
            target = self._lightning_clamp_to_donut(
                (origin[0] + perp[0] * LIGHTNING_VEE_DEPTH * sign,
                 origin[1] + perp[1] * LIGHTNING_VEE_DEPTH * sign)
            )
            state = {
                "phase": "OUT",
                "leg": 0,
                "origin": origin,
                "target": target,
            }
            self.memory.lightning_vee_state[uid] = state
        if state["phase"] == "OUT":
            target = state["target"]
            if _distance(vanguard.position, target) <= LIGHTNING_VEE_REACH_TOLERANCE:
                # 到达最远 → 翻 INBOUND,终点为当前 Core 位置。
                state["phase"] = "IN"
                state["target"] = core.position
            # 出框保护:若 origin→target 投影后距离远小于 VEE_DEPTH(clamp 被截),
            # 视为被框边拦住,提前翻回。
            elif _distance(state["origin"], target) < LIGHTNING_VEE_DEPTH - LIGHTNING_VEE_REACH_TOLERANCE:
                state["phase"] = "IN"
                state["target"] = core.position
        else:  # IN
            state["target"] = core.position
            if _distance(vanguard.position, core.position) <= LIGHTNING_VEE_HOME_TOLERANCE:
                # 到家 → 翻 OUTBOUND,leg 翻转,origin 重设为当前 Core 位置。
                state["phase"] = "OUT"
                state["leg"] = (state["leg"] + 1) % 2
                state["origin"] = core.position
                sign = 1 if state["leg"] == 0 else -1
                state["target"] = self._lightning_clamp_to_donut(
                    (core.position[0] + perp[0] * LIGHTNING_VEE_DEPTH * sign,
                     core.position[1] + perp[1] * LIGHTNING_VEE_DEPTH * sign)
                )
        target = state["target"]
        if target == vanguard.position:
            return None
        return target

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
        if self.memory.recall:
            self._choose_vanguards_recall(turn, planner, acted_units, decisions)
        elif self.memory.rally_point is not None:
            self._choose_vanguards_rally(turn, planner, acted_units, decisions)
        elif False:
            self._choose_vanguards_aggress(turn, planner, acted_units, decisions)
        elif False:
            self._choose_vanguards_beacon(turn, planner, acted_units, decisions)
        elif False:
            self._choose_vanguards_migrate(turn, planner, acted_units, decisions)
        elif False:
            self._choose_vanguards_develop(turn, planner, acted_units, decisions)
        elif True:
            self._choose_vanguards_lightning(turn, planner, acted_units, decisions)
        else:
            self._choose_vanguards_defend(turn, planner, acted_units, decisions)

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

    def _pick_enemy_core_target(self, turn: Turn) -> Position | None:
        origin = turn.core.position if turn.core is not None else (0, 0)
        raid_core_id = self.memory.raid_core_id if self.memory.raid_enabled else None
        visible_cores = [
            enemy
            for enemy in turn.visible_enemies
            if isinstance(enemy, CoreView) and str(enemy.id) != raid_core_id
        ]
        if visible_cores:
            nearest = min(
                visible_cores,
                key=lambda enemy: (_distance(origin, enemy.position), enemy.id.bytes),
            )
            return nearest.position

        remembered_cores = [
            sighting
            for object_id, sighting in self.memory.enemy_sightings.items()
            if sighting.is_core and object_id != raid_core_id
        ]
        if remembered_cores:
            sighting = min(
                remembered_cores,
                key=lambda candidate: (
                    turn.tick - candidate.seen_tick,
                    _distance(origin, candidate.position),
                    candidate.position,
                ),
            )
            return sighting.position
        return None

    def _pick_assault_target(self, turn: Turn) -> Position | None:
        core_target = self._pick_enemy_core_target(turn)
        if core_target is not None:
            return core_target

        origin = turn.core.position if turn.core is not None else (0, 0)
        raid_core_id = self.memory.raid_core_id if self.memory.raid_enabled else None
        visible_targets = tuple(
            enemy
            for enemy in turn.visible_enemies
            if str(enemy.id) != raid_core_id
        )

        if visible_targets:
            nearest = min(
                visible_targets,
                key=lambda enemy: (
                    _enemy_role_priority(enemy),
                    _distance(origin, enemy.position),
                    enemy.id.bytes,
                ),
            )
            return nearest.position

        remembered_targets = [
            sighting
            for object_id, sighting in self.memory.enemy_sightings.items()
            if object_id != raid_core_id
        ]
        if not remembered_targets:
            return None
        sighting = min(
            remembered_targets,
            key=lambda candidate: (
                turn.tick - candidate.seen_tick,
                _distance(origin, candidate.position),
                candidate.position,
            ),
        )
        return sighting.position

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
        raid_ids = self.memory.raid_vanguard_ids | self.memory.raid_ranger_ids
        forward_allies = [
            unit
            for unit in (*turn.vanguards, *turn.rangers)
            if unit.id != carrier.id
            and unit.id not in defender_ids
            and str(unit.id) not in raid_ids
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
        raid_ids = self.memory.raid_vanguard_ids | self.memory.raid_ranger_ids
        assault_units = tuple(
            unit
            for unit in (*turn.vanguards, *turn.rangers)
            if unit.id not in excluded_ids
            and str(unit.id) not in raid_ids
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

    def _predicted_enemy_cell(
        self,
        turn: Turn,
        enemy: UnitView | CoreView,
    ) -> Position:
        """预判敌人下一 tick 位置：沿最近一次移动方向外推一格。"""
        current = enemy.position
        if isinstance(enemy, CoreView):
            return current
        prev = self.memory.enemy_prev.get(str(enemy.id))
        if prev is None:
            return current
        dx = current[0] - prev[0]
        dy = current[1] - prev[1]
        if abs(dx) > 1 or abs(dy) > 1 or (dx != 0 and dy != 0):
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

    def _ranger_shot_candidates(
        self,
        turn: Turn,
        ranger: Ranger,
        planner: MovementPlanner,
    ) -> list[tuple[UnitView | CoreView, Position]]:
        """返回 (敌人, 射击格) 候选，并协调同 Tick 的火力覆盖。"""
        candidates: list[tuple[UnitView | CoreView, Position]] = []
        for enemy in turn.visible_enemies:
            target_key = str(enemy.id)
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
            candidates.append(
                (
                    enemy,
                    min(
                        legal_cells,
                        key=lambda cell: (
                            1
                            if coverage_active
                            and (target_key, cell) in self.memory.current_shot_cells
                            else 0,
                            self.memory.shot_miss_counts.get(
                                _shot_cell_key(enemy.id, cell),
                                0,
                            ),
                            legal_cells.index(cell),
                            cell,
                        ),
                    ),
                )
            )
        return candidates

    def _mark_ranger_shot(
        self,
        target: UnitView | CoreView,
        cell: Position,
    ) -> None:
        target_key = str(target.id)
        if cell != target.position or self.memory.shot_miss_counts.get(
            _shot_cell_key(target.id, cell),
            0,
        ):
            self.memory.decision_totals["ranger:shot_coverage"] += 1
        self.memory.current_shot_cells.add((target_key, cell))

    def _choose_vanguards_beacon(
        self,
        turn: Turn,
        planner: MovementPlanner,
        acted_units: set[UUID],
        decisions: list[str],
    ) -> None:
        beacon_position = turn.beacon.position
        home_vanguards, home_rangers = self._beacon_home_reserve_ids(turn)
        (
            local_core_target,
            local_sortie_vanguards,
            local_sortie_rangers,
        ) = self._beacon_local_core_sortie_assignments(
            turn,
            home_vanguards,
            home_rangers,
            decisions,
        )
        protected_vanguards = home_vanguards | local_sortie_vanguards
        protected_rangers = home_rangers | local_sortie_rangers
        if local_core_target is not None:
            self._choose_beacon_local_core_sortie_vanguards(
                turn,
                planner,
                acted_units,
                decisions,
                local_core_target,
                local_sortie_vanguards,
            )
        core_target = self._beacon_core_assault_target(
            turn,
            protected_vanguards,
            protected_rangers,
        )
        strategic_target = core_target or beacon_position
        order = self._beacon_expedition_order(
            turn,
            planner,
            protected_vanguards,
            protected_rangers,
            strategic_target,
            core_target=core_target,
            excluded_ids=acted_units,
        )
        expedition_vanguards = [
            unit
            for unit in turn.vanguards
            if unit.id not in protected_vanguards and unit.id not in acted_units
        ]
        formation_slots = self._beacon_guard_slots(
            turn,
            planner,
            order.formation_anchor,
            expedition_vanguards,
            BEACON_EXPEDITION_VANGUARD_OFFSETS,
            evenly_spaced=True,
        )
        decisions.append(
            "beacon_expedition_order "
            f"phase={order.phase} target={order.strategic_target} "
            f"anchor={order.formation_anchor} "
            f"enemy_combat={order.enemy_combat_units}"
        )
        self.memory.decision_totals[
            f"beacon:expedition_{order.phase}"
        ] += 1
        if core_target is not None:
            decisions.append(f"beacon_enemy_core_priority target={core_target}")
            self.memory.decision_totals[
                "beacon:enemy_core_priority"
            ] += 1
        for vanguard in sorted(turn.vanguards, key=_uuid_key):
            if vanguard.id in acted_units or vanguard.id in protected_vanguards:
                continue
            if (
                order.phase in BEACON_EXPEDITION_FORMATION_PRIORITY_PHASES
                and self._move_beacon_expedition_unit(
                    turn,
                    planner,
                    vanguard,
                    order,
                    formation_slots,
                    decisions,
                )
            ):
                continue
            direction = self._sweep_targets(
                vanguard,
                turn,
                include_workers=core_target is None,
            )
            if direction is not None:
                vanguard.sweep(direction)
                decisions.append(
                    f"vanguard:{_short_id(vanguard.id)} sweep {direction.value} reason=beacon"
                )
                self.memory.decision_totals["vanguard:sweep"] += 1
                continue
            if (
                turn.core is not None
                and len(home_vanguards) < RAID_HOME_RESERVE_VANGUARDS
            ):
                # Before the fixed home screen is complete, every available
                # Vanguard helps. Once it is complete, surplus expedition
                # Vanguards keep their strategic target.
                threatening = [
                    enemy
                    for enemy in turn.visible_enemies
                    if _distance(enemy.position, turn.core.position) <= 5
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
                    planner.toward(vanguard, target.position, "beacon_defend_core")
                    continue
            if (
                core_target is not None
                and order.phase == "weak_core_strike"
                and vanguard.id in order.assault_ids
            ):
                planner.toward(vanguard, core_target, "enemy_core_assault")
                decisions.append(
                    f"vanguard:{_short_id(vanguard.id)} enemy_core_assault "
                    f"target={core_target} role=beacon_surplus"
                )
                self.memory.decision_totals[
                    "vanguard:enemy_core_assault"
                ] += 1
                continue
            self._move_beacon_expedition_unit(
                turn,
                planner,
                vanguard,
                order,
                formation_slots,
                decisions,
            )
        self._choose_vanguards_defend(
            turn,
            planner,
            acted_units,
            decisions,
            eligible_ids=home_vanguards - local_sortie_vanguards,
        )

    def _choose_vanguards_develop(
        self,
        turn: Turn,
        planner: MovementPlanner,
        acted_units: set[UUID],
        decisions: list[str],
    ) -> None:
        scout_vanguards, _ = self._develop_beacon_scout_ids(turn)
        for vanguard in sorted(turn.vanguards, key=_uuid_key):
            if vanguard.id not in scout_vanguards or vanguard.id in acted_units:
                continue
            direction = self._sweep_targets(vanguard, turn)
            if direction is not None:
                vanguard.sweep(direction)
                decisions.append(
                    f"vanguard:{_short_id(vanguard.id)} sweep {direction.value} "
                    "reason=develop_beacon_scout"
                )
                self.memory.decision_totals["vanguard:sweep"] += 1
                continue
            if planner.toward(
                vanguard,
                turn.beacon.position,
                "develop_beacon_vanguard",
            ):
                decisions.append(
                    f"vanguard:{_short_id(vanguard.id)} beacon_head_start "
                    f"target={turn.beacon.position}"
                )
                self.memory.decision_totals["beacon:early_vanguard_advance"] += 1

        home_vanguards = {
            unit.id for unit in turn.vanguards if unit.id not in scout_vanguards
        }
        self._choose_vanguards_defend(
            turn,
            planner,
            acted_units,
            decisions,
            eligible_ids=home_vanguards,
        )

    def _choose_vanguards_aggress(
        self,
        turn: Turn,
        planner: MovementPlanner,
        acted_units: set[UUID],
        decisions: list[str],
    ) -> None:
        ordered = sorted(turn.vanguards, key=_uuid_key)
        carrier, beacon_vanguard_guard_ids, beacon_ranger_guard_ids = (
            self._aggress_beacon_guard_assignments(turn)
        )
        home_reserve_vanguards, _ = self._aggress_action_reserve_ids(
            turn,
            carrier=carrier,
            beacon_vanguard_guards=beacon_vanguard_guard_ids,
            beacon_ranger_guards=beacon_ranger_guard_ids,
        )
        home_recovery = (
            len(turn.vanguards) < RAID_HOME_RESERVE_VANGUARDS
            or len(turn.rangers) < RAID_HOME_RESERVE_RANGERS
        )
        if home_recovery and self._pick_enemy_core_target(turn) is not None:
            # The 3+3 reserve is a minimum.  Until it is restored, all
            # surviving combat units screen the Core rather than chase a known
            # enemy Core or a distant frontier.
            home_reserve_vanguards = {unit.id for unit in ordered}
        defender_ids, ranger_defender_ids = self._aggress_core_defender_ids(turn)
        reinforcement_active, reinforcement_threats = (
            self._aggress_core_reinforcement_state(turn)
        )
        guard_ids = home_reserve_vanguards or defender_ids
        defenders = [unit for unit in ordered if unit.id in guard_ids]
        core_alert = bool(
            turn.core is not None
            and any(
                _distance(enemy.position, turn.core.position)
                <= AGGRESS_CORE_ALERT_RADIUS
                for enemy in turn.visible_enemies
            )
        )
        core_guard_slots = self._beacon_guard_slots(
            turn,
            planner,
            turn.core.position if turn.core is not None else (0, 0),
            defenders,
            _terrain_guard_offsets(
                turn.core.position if turn.core is not None else (0, 0),
                planner.obstacles,
                AGGRESS_VANGUARD_ALERT_OFFSETS
                if core_alert
                else AGGRESS_VANGUARD_WATCH_OFFSETS,
            ),
        )
        core_target = self._pick_enemy_core_target(turn)
        combat_target = core_target or self._pick_assault_target(turn)
        core_priority_active = core_target is not None
        (
            core_assault_ready,
            core_assault_vanguards,
            _,
            core_assault_rally,
        ) = self._core_assault_assignments(turn, core_target)
        # Keep the discovered coordinate, but never let an incomplete home
        # screen act on it.  It will resume normal Core-assault staging only
        # after the fixed 3+3 garrison is rebuilt.
        if home_recovery and core_target is not None:
            combat_target = None
        if core_priority_active and combat_target is not None:
            decisions.append(f"enemy_core_priority target={combat_target}")
            self.memory.decision_totals["assault:enemy_core_priority"] += 1
            if core_assault_rally is not None:
                decisions.append(
                    "enemy_core_assault_"
                    f"{'ready' if core_assault_ready else 'rally'} "
                    f"target={core_target} rally={core_assault_rally} "
                    f"vanguards={len(core_assault_vanguards)}"
                )
        frontier_target = (
            None
            if core_priority_active
            else self._assault_frontier_target(turn, planner)
        )
        now = turn.tick
        # 广播系统：最近 40 tick 内被攻击的队友（含先锋/游侠）
        attacked_victims: list[tuple[Position, UUID]] = []
        for unit in list(turn.vanguards) + list(turn.rangers):
            attacked_tick = self.memory.attacked_units.get(str(unit.id))
            if attacked_tick is not None and now - attacked_tick <= 40:
                attacked_victims.append((unit.position, unit.id))
        victim_positions = [position for position, _ in attacked_victims]

        # 编队方向：游侠 leader 的推进目标（combat 优先，其次 frontier/信标）
        squad_direction: Position | None = combat_target or frontier_target

        beacon_guard_slots: dict[UUID, Position] = {}
        beacon_guard_threats: list[UnitView] = []
        beacon_vanguard_interceptor_id: UUID | None = None
        if carrier is not None:
            self._choose_aggress_beacon_carrier(
                turn,
                planner,
                carrier,
                beacon_vanguard_guard_ids,
                beacon_ranger_guard_ids,
                defender_ids,
                ranger_defender_ids,
                combat_target,
                frontier_target,
                acted_units,
                decisions,
            )
            guard_units = [
                unit
                for unit in ordered
                if unit.id in beacon_vanguard_guard_ids
            ]
            beacon_guard_threats = [
                enemy
                for enemy in turn.visible_enemies
                if _distance(enemy.position, carrier.position)
                <= BEACON_GUARD_THREAT_RADIUS
            ]
            if beacon_guard_threats and guard_units:
                beacon_vanguard_interceptor_id = min(
                    guard_units,
                    key=lambda guard: (
                        min(
                            _distance(guard.position, enemy.position)
                            for enemy in beacon_guard_threats
                        ),
                        guard.id.bytes,
                    ),
                ).id
            beacon_guard_slots = self._beacon_guard_slots(
                turn,
                planner,
                self._beacon_guard_anchor(carrier, turn.tick),
                guard_units,
                BEACON_VANGUARD_GUARD_OFFSETS,
                rotation=turn.tick // BEACON_GUARD_PATROL_TICKS,
                evenly_spaced=True,
            )

        for vanguard in ordered:
            if vanguard.id in acted_units:
                continue
            if vanguard.id in home_reserve_vanguards:
                home_sweep = next(
                    (
                        candidate
                        for candidate in DIRECTION_ORDER
                        if any(
                            not isinstance(enemy, CoreView)
                            and _destination(vanguard.position, candidate)
                            == enemy.position
                            and turn.core is not None
                            and _distance(enemy.position, turn.core.position)
                            <= AGGRESS_CORE_ALERT_RADIUS
                            for enemy in turn.visible_enemies
                        )
                    ),
                    None,
                )
                if home_sweep is not None:
                    vanguard.sweep(home_sweep)
                    decisions.append(
                        f"vanguard:{_short_id(vanguard.id)} sweep "
                        f"{home_sweep.value} reason=home_reserve_defend"
                    )
                    self.memory.decision_totals["vanguard:home_reserve_defend"] += 1
                    continue
                guard_slot = core_guard_slots.get(vanguard.id)
                if guard_slot is not None and vanguard.position != guard_slot:
                    planner.toward(
                        vanguard,
                        guard_slot,
                        "aggress_core_contract"
                        if core_alert
                        else "aggress_core_watch",
                    )
                self.memory.decision_totals["vanguard:aggress_guard"] += 1
                continue
            if (
                core_target is not None
                and vanguard.id in core_assault_vanguards
                and not core_assault_ready
                and core_assault_rally is not None
            ):
                planner.toward(vanguard, core_assault_rally, "enemy_core_rally")
                decisions.append(
                    f"vanguard:{_short_id(vanguard.id)} enemy_core_rally "
                    f"target={core_target} rally={core_assault_rally}"
                )
                self.memory.decision_totals["vanguard:enemy_core_rally"] += 1
                continue
            if (
                core_target is not None
                and core_assault_ready
                and vanguard.id in core_assault_vanguards
                and vanguard.id not in beacon_vanguard_guard_ids
            ):
                core_visible = any(
                    isinstance(enemy, CoreView)
                    and enemy.position == core_target
                    for enemy in turn.visible_enemies
                )
                direction = next(
                    (
                        candidate
                        for candidate in DIRECTION_ORDER
                        if _destination(vanguard.position, candidate) == core_target
                    ),
                    None,
                )
                if core_visible and direction is not None:
                    vanguard.sweep(direction)
                    decisions.append(
                        f"vanguard:{_short_id(vanguard.id)} sweep "
                        f"{direction.value} reason=enemy_core_priority"
                    )
                    self.memory.decision_totals[
                        "vanguard:enemy_core_priority_sweep"
                    ] += 1
                else:
                    planner.toward(
                        vanguard,
                        core_target,
                        "enemy_core_assault",
                    )
                    decisions.append(
                        f"vanguard:{_short_id(vanguard.id)} enemy_core_assault "
                        f"target={core_target}"
                    )
                    self.memory.decision_totals[
                        "vanguard:enemy_core_assault"
                    ] += 1
                continue
            if vanguard.id in beacon_vanguard_guard_ids and carrier is not None:
                direction = self._sweep_targets(vanguard, turn)
                if direction is not None:
                    vanguard.sweep(direction)
                    decisions.append(
                        f"vanguard:{_short_id(vanguard.id)} sweep {direction.value} "
                        "reason=beacon_guard"
                    )
                    self.memory.decision_totals["beacon_guard:vanguard_sweep"] += 1
                    continue
                if (
                    beacon_guard_threats
                    and vanguard.id == beacon_vanguard_interceptor_id
                ):
                    threat = min(
                        beacon_guard_threats,
                        key=lambda enemy: (
                            _enemy_role_priority(enemy),
                            _distance(vanguard.position, enemy.position),
                            enemy.id.bytes,
                        ),
                    )
                    planner.toward(
                        vanguard,
                        threat.position,
                        "beacon_vanguard_intercept",
                    )
                    self.memory.decision_totals["beacon_guard:vanguard_intercept"] += 1
                    continue
                slot = beacon_guard_slots.get(vanguard.id, carrier.position)
                if vanguard.position != slot:
                    planner.toward(
                        vanguard,
                        slot,
                        "beacon_vanguard_guard_patrol",
                    )
                else:
                    vanguard.wait()
                self.memory.decision_totals["beacon_guard:vanguard_patrol"] += 1
                continue
            if (
                reinforcement_active
                and turn.core is not None
                and vanguard.id not in defender_ids
            ):
                adjacent_threats = [
                    enemy
                    for enemy in reinforcement_threats
                    if _distance(vanguard.position, enemy.position) == 1
                ]
                if adjacent_threats:
                    threat = min(
                        adjacent_threats,
                        key=lambda enemy: (
                            _enemy_role_priority(enemy),
                            enemy.hp,
                            enemy.id.bytes,
                        ),
                    )
                    direction = next(
                        direction
                        for direction in DIRECTION_ORDER
                        if _destination(vanguard.position, direction)
                        == threat.position
                    )
                    vanguard.sweep(direction)
                    decisions.append(
                        f"vanguard:{_short_id(vanguard.id)} sweep "
                        f"{direction.value} reason=core_reinforce"
                    )
                    self.memory.decision_totals[
                        "core_reinforcement:vanguard_sweep"
                    ] += 1
                    continue
                if reinforcement_threats:
                    target = min(
                        reinforcement_threats,
                        key=lambda enemy: (
                            _distance(enemy.position, turn.core.position),
                            _enemy_role_priority(enemy),
                            _distance(vanguard.position, enemy.position),
                            enemy.id.bytes,
                        ),
                    ).position
                else:
                    attackers = [
                        unit
                        for unit in ordered
                        if unit.id not in defender_ids
                        and unit.id not in beacon_vanguard_guard_ids
                        and (carrier is None or unit.id != carrier.id)
                    ]
                    offset = VANGUARD_RECALL_OFFSETS[
                        attackers.index(vanguard) % len(VANGUARD_RECALL_OFFSETS)
                    ]
                    target = (
                        turn.core.position[0] + offset[0],
                        turn.core.position[1] + offset[1],
                    )
                if vanguard.position != target:
                    planner.toward(
                        vanguard,
                        target,
                        "aggress_core_reinforce",
                    )
                else:
                    vanguard.wait()
                decisions.append(
                    f"vanguard:{_short_id(vanguard.id)} core_reinforce "
                    f"target={target}"
                )
                self.memory.decision_totals[
                    "core_reinforcement:vanguard_return"
                ] += 1
                continue
            vanguard_key = str(vanguard.id)
            # 1. 自己被攻击且敌人贴身 → 撤退回走位（不原地挨打）
            if (
                vanguard_key in self.memory.attacked_units
                and now - self.memory.attacked_units[vanguard_key] <= 30
                and turn.visible_enemies
            ):
                nearest_enemy = min(
                    turn.visible_enemies,
                    key=lambda enemy: _distance(enemy.position, vanguard.position),
                )
                if _distance(nearest_enemy.position, vanguard.position) <= 6:
                    retreat = (
                        vanguard.position[0] * 2 - nearest_enemy.position[0],
                        vanguard.position[1] * 2 - nearest_enemy.position[1],
                    )
                    planner.toward(vanguard, retreat, "aggress_retreat")
                    decisions.append(
                        f"vanguard:{_short_id(vanguard.id)} retreat "
                        f"from={_short_id(nearest_enemy.id)}"
                    )
                    self.memory.decision_totals["vanguard:retreat"] += 1
                    continue
            # 2. 贴脸敌人 → sweep（近身战斗）
            direction = self._sweep_targets(
                vanguard,
                turn,
                include_core=vanguard.id not in defender_ids,
            )
            if direction is not None:
                vanguard.sweep(direction)
                decisions.append(
                    f"vanguard:{_short_id(vanguard.id)} sweep {direction.value} reason=aggress"
                )
                self.memory.decision_totals["vanguard:sweep"] += 1
                continue
            # 3. 家被摸 → 先救家
            if turn.core is not None:
                threatening = [
                    enemy
                    for enemy in turn.visible_enemies
                    if not isinstance(enemy, CoreView)
                    if _distance(enemy.position, turn.core.position) <= 5
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
                    planner.toward(vanguard, target.position, "aggress_defend_core")
                    continue
            # 4. 守家单位：安全时分散预警，发现敌情后收缩到 Core。
            # 5. 支援被攻击的队友（靠近的优先，站在受害者附近拦截）
            if victim_positions and not core_priority_active:
                nearest_victim = min(
                    victim_positions,
                    key=lambda position: _distance(vanguard.position, position),
                )
                victim_distance = _distance(vanguard.position, nearest_victim)
                if 2 < victim_distance <= 18:
                    planner.toward(vanguard, nearest_victim, "aggress_support")
                    decisions.append(
                        f"vanguard:{_short_id(vanguard.id)} support "
                        f"victim={nearest_victim}"
                    )
                    self.memory.decision_totals["vanguard:support"] += 1
                    continue
            # 6. 编队（核心）：先锋站到游侠前方 2 格（游侠与目标方向之间）
            rangers = [
                r
                for r in turn.rangers
                if r.id not in acted_units
                and r.id not in beacon_ranger_guard_ids
                and r.id not in ranger_defender_ids
            ]
            if rangers:
                buddy = min(
                    rangers,
                    key=lambda r: _distance(vanguard.position, r.position),
                )
                buddy_position = buddy.position
                if squad_direction is not None:
                    # 先锋站位 = 游侠朝向目标方向前推 2 格（挡在游侠与敌人之间）
                    dx = _sign(squad_direction[0] - buddy_position[0])
                    dy = _sign(squad_direction[1] - buddy_position[1])
                    formation = (
                        buddy_position[0] + dx * 2,
                        buddy_position[1] + dy * 2,
                    )
                else:
                    formation = buddy_position
                if _distance(vanguard.position, formation) > 1:
                    planner.toward(vanguard, formation, "vanguard_squad_front")
                    decisions.append(
                        f"vanguard:{_short_id(vanguard.id)} squad_front "
                        f"ranger:{_short_id(buddy.id)} pos={formation}"
                    )
                    self.memory.decision_totals["vanguard:squad_front"] += 1
                continue
            # 7. 无游侠可护卫 → 向目标推进
            if squad_direction is not None:
                planner.toward(vanguard, squad_direction, "aggress_advance")
                self.memory.decision_totals["vanguard:frontier"] += 1

    def _choose_vanguards_rally(
        self,
        turn: Turn,
        planner: MovementPlanner,
        acted_units: set[UUID],
        decisions: list[str],
    ) -> None:
        rally = self.memory.rally_point
        if rally is None:
            return
        for vanguard in sorted(turn.vanguards, key=_uuid_key):
            if vanguard.id in acted_units:
                continue
            direction = self._sweep_targets(vanguard, turn)
            if direction is not None:
                vanguard.sweep(direction)
                decisions.append(
                    f"vanguard:{_short_id(vanguard.id)} sweep {direction.value} reason=rally"
                )
                self.memory.decision_totals["vanguard:sweep"] += 1
                continue
            if turn.core is not None:
                threatening = [
                    enemy
                    for enemy in turn.visible_enemies
                    if _distance(enemy.position, turn.core.position) <= 5
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
                    planner.toward(vanguard, target.position, "rally_defend_core")
                    continue
            if _distance(vanguard.position, rally) > 1:
                planner.toward(vanguard, rally, "rally_advance")
                decisions.append(
                    f"vanguard:{_short_id(vanguard.id)} rally_advance "
                    f"target={rally}"
                )
                self.memory.decision_totals["vanguard:rally"] += 1

    def _choose_rangers_rally(
        self,
        turn: Turn,
        planner: MovementPlanner,
        acted_units: set[UUID],
        decisions: list[str],
    ) -> None:
        assigned_damage: Counter[UUID] = Counter()
        rally = self.memory.rally_point
        if rally is None:
            return
        for ranger in sorted(turn.rangers, key=_uuid_key):
            if ranger.id in acted_units:
                continue
            shot_candidates = self._ranger_shot_candidates(turn, ranger, planner)
            if shot_candidates:
                target, cell = min(
                    shot_candidates,
                    key=lambda pair: (
                        1 if assigned_damage[pair[0].id] >= _effective_hp(pair[0]) else 0,
                        0 if isinstance(pair[0], CoreView) else 1,
                        _enemy_role_priority(pair[0]),
                        _effective_hp(pair[0]),
                        _distance(ranger.position, pair[0].position),
                        pair[0].id.bytes,
                    ),
                )
                ranger.shoot(target, expected_cell=cell)
                self._mark_ranger_shot(target, cell)
                decisions.append(
                    f"ranger:{_short_id(ranger.id)} shoot target={_short_id(target.id)} "
                    f"expected={cell} role=rally"
                )
                self.memory.decision_totals["ranger:shoot"] += 1
                continue
            firing_cells = self._firing_cells(rally, planner.obstacles)
            if firing_cells:
                firing_cell = min(
                    firing_cells,
                    key=lambda position: (
                        planner.threat.get(position, 0),
                        _distance(ranger.position, position),
                        position,
                    ),
                )
                planner.toward(ranger, firing_cell, "rally_seek_firing")
            else:
                planner.toward(ranger, rally, "rally_advance")
            self.memory.decision_totals["ranger:rally"] += 1

    def _choose_vanguards_recall(
        self,
        turn: Turn,
        planner: MovementPlanner,
        acted_units: set[UUID],
        decisions: list[str],
        excluded_ids: set[UUID] | None = None,
    ) -> None:
        excluded = excluded_ids or set()
        ordered_vanguards = sorted(
            (unit for unit in turn.vanguards if unit.id not in excluded),
            key=_uuid_key,
        )
        core_position = turn.core.position if turn.core is not None else (0, 0)
        ordered_offsets = _terrain_guard_offsets(
            core_position,
            planner.obstacles,
            VANGUARD_RECALL_OFFSETS,
        )
        logistics_corridor = _core_logistics_corridor(
            core_position,
            planner.obstacles,
        )
        recall_offsets = tuple(
            offset
            for offset in ordered_offsets
            if (
                (core_position[0] + offset[0], core_position[1] + offset[1])
                not in planner.obstacles
                and (
                    core_position[0] + offset[0],
                    core_position[1] + offset[1],
                )
                not in logistics_corridor
            )
        )
        if not recall_offsets:
            recall_offsets = tuple(
                offset
                for offset in ordered_offsets
                if (
                    core_position[0] + offset[0],
                    core_position[1] + offset[1],
                )
                not in planner.obstacles
            ) or ordered_offsets
        vanguard_indexes = {
            unit.id: index for index, unit in enumerate(ordered_vanguards)
        }
        for vanguard in ordered_vanguards:
            if vanguard.id in acted_units:
                continue
            direction = self._sweep_targets(vanguard, turn)
            if direction is not None:
                vanguard.sweep(direction)
                decisions.append(
                    f"vanguard:{_short_id(vanguard.id)} sweep {direction.value} reason=recall"
                )
                self.memory.decision_totals["vanguard:sweep"] += 1
                continue
            if turn.core is not None:
                threatening = [
                    enemy
                    for enemy in turn.visible_enemies
                    if _distance(enemy.position, turn.core.position) <= 8
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
                    planner.toward(vanguard, target.position, "recall_intercept")
                    continue
                if (
                    _distance(vanguard.position, turn.core.position) > 1
                    or vanguard.position in logistics_corridor
                ):
                    offset = recall_offsets[
                        (index := vanguard_indexes[vanguard.id])
                        % len(recall_offsets)
                    ]
                    target = (
                        turn.core.position[0] + offset[0],
                        turn.core.position[1] + offset[1],
                    )
                    planner.toward(vanguard, target, "recall_guard_core")
                    self.memory.decision_totals["vanguard:recall"] += 1

    def _choose_vanguards_migrate(
        self,
        turn: Turn,
        planner: MovementPlanner,
        acted_units: set[UUID],
        decisions: list[str],
    ) -> None:
        carrier, vanguard_guard_ids, _ = self._aggress_beacon_guard_assignments(turn)
        protected_ids = set(vanguard_guard_ids)
        if carrier is not None:
            protected_ids.add(carrier.id)
            if carrier.id not in acted_units:
                threats = [
                    enemy.position
                    for enemy in turn.visible_enemies
                    if _distance(enemy.position, carrier.position)
                    <= BEACON_CARRIER_DANGER_RADIUS
                ]
                moved = False
                if threats or carrier.hp * 2 < MAX_HP[UnitType.VANGUARD]:
                    moved = planner.flee_open(
                        carrier,
                        threats,
                        turn.core.position if turn.core is not None else None,
                        "migration_beacon_escape",
                    )
                if not moved:
                    carrier.wait()
                acted_units.add(carrier.id)
                decisions.append(
                    f"vanguard:{_short_id(carrier.id)} migration_beacon_hold "
                    f"threats={len(threats)}"
                )
                self.memory.decision_totals["migration:beacon_carrier_hold"] += 1

            guards = [
                unit for unit in turn.vanguards if unit.id in vanguard_guard_ids
            ]
            slots = self._beacon_guard_slots(
                turn,
                planner,
                carrier.position,
                guards,
                BEACON_VANGUARD_GUARD_OFFSETS,
            )
            for guard in guards:
                if guard.id in acted_units:
                    continue
                direction = self._sweep_targets(guard, turn)
                if direction is not None:
                    guard.sweep(direction)
                    acted_units.add(guard.id)
                    continue
                slot = slots.get(guard.id, carrier.position)
                if guard.position == slot:
                    guard.wait()
                    acted_units.add(guard.id)
                elif planner.toward(guard, slot, "migration_beacon_vanguard_guard"):
                    acted_units.add(guard.id)

        self._choose_vanguards_recall(
            turn,
            planner,
            acted_units,
            decisions,
            excluded_ids=protected_ids,
        )

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
        if self.memory.recall:
            self._choose_rangers_recall(turn, planner, acted_units, decisions)
        elif self.memory.rally_point is not None:
            self._choose_rangers_rally(turn, planner, acted_units, decisions)
        elif False:
            self._choose_rangers_aggress(turn, planner, acted_units, decisions)
        elif False:
            self._choose_rangers_beacon(turn, planner, acted_units, decisions)
        elif False:
            self._choose_rangers_migrate(turn, planner, acted_units, decisions)
        elif False:
            self._choose_rangers_develop(turn, planner, acted_units, decisions)
        elif True:
            self._choose_rangers_lightning(turn, planner, acted_units, decisions)
        else:
            self._choose_rangers_defend(turn, planner, acted_units, decisions)

    def _choose_rangers_aggress(
        self,
        turn: Turn,
        planner: MovementPlanner,
        acted_units: set[UUID],
        decisions: list[str],
    ) -> None:
        assigned_damage: Counter[UUID] = Counter()
        ordered = sorted(turn.rangers, key=_uuid_key)
        carrier, _, beacon_ranger_guard_ids = (
            self._aggress_beacon_guard_assignments(turn)
        )
        beacon_carrier, beacon_vanguard_guard_ids, _ = (
            self._aggress_beacon_guard_assignments(turn)
        )
        _, home_reserve_rangers = self._aggress_action_reserve_ids(
            turn,
            carrier=beacon_carrier,
            beacon_vanguard_guards=beacon_vanguard_guard_ids,
            beacon_ranger_guards=beacon_ranger_guard_ids,
        )
        home_recovery = (
            len(turn.vanguards) < RAID_HOME_RESERVE_VANGUARDS
            or len(turn.rangers) < RAID_HOME_RESERVE_RANGERS
        )
        if home_recovery and self._pick_enemy_core_target(turn) is not None:
            home_reserve_rangers = {unit.id for unit in ordered}
        _, defender_ids = self._aggress_core_defender_ids(turn)
        reinforcement_active, reinforcement_threats = (
            self._aggress_core_reinforcement_state(turn)
        )
        guard_ids = home_reserve_rangers or defender_ids
        defenders = [unit for unit in ordered if unit.id in guard_ids]
        core_alert = bool(
            turn.core is not None
            and any(
                _distance(enemy.position, turn.core.position)
                <= AGGRESS_CORE_ALERT_RADIUS
                for enemy in turn.visible_enemies
            )
        )
        patrol_slots = self._beacon_guard_slots(
            turn,
            planner,
            turn.core.position if turn.core is not None else (0, 0),
            defenders,
            _terrain_guard_offsets(
                turn.core.position if turn.core is not None else (0, 0),
                planner.obstacles,
                AGGRESS_RANGER_ALERT_OFFSETS
                if core_alert
                else AGGRESS_RANGER_WATCH_OFFSETS,
            ),
        )
        core_target = self._pick_enemy_core_target(turn)
        combat_target = core_target or self._pick_assault_target(turn)
        core_priority_active = core_target is not None
        (
            core_assault_ready,
            _,
            core_assault_rangers,
            core_assault_rally,
        ) = self._core_assault_assignments(turn, core_target)
        if home_recovery and core_target is not None:
            combat_target = None
        frontier_target = (
            None
            if core_priority_active
            else self._assault_frontier_target(turn, planner)
        )
        frontier_probe_count = 0
        now = turn.tick
        # 广播系统：最近 40 tick 内被攻击的队友（含先锋/游侠）
        attacked_victims: list[Position] = []
        for unit in list(turn.vanguards) + list(turn.rangers):
            attacked_tick = self.memory.attacked_units.get(str(unit.id))
            if attacked_tick is not None and now - attacked_tick <= 40:
                attacked_victims.append(unit.position)
        beacon_guard_slots: dict[UUID, Position] = {}
        beacon_guard_threats: list[UnitView] = []
        beacon_ranger_interceptor_id: UUID | None = None
        if carrier is not None:
            guard_units = [
                unit for unit in ordered if unit.id in beacon_ranger_guard_ids
            ]
            beacon_guard_threats = [
                enemy
                for enemy in turn.visible_enemies
                if _distance(enemy.position, carrier.position)
                <= BEACON_GUARD_THREAT_RADIUS
            ]
            if beacon_guard_threats and guard_units:
                beacon_ranger_interceptor_id = min(
                    guard_units,
                    key=lambda guard: (
                        min(
                            _distance(guard.position, enemy.position)
                            for enemy in beacon_guard_threats
                        ),
                        guard.id.bytes,
                    ),
                ).id
            beacon_guard_slots = self._beacon_guard_slots(
                turn,
                planner,
                self._beacon_guard_anchor(carrier, turn.tick),
                guard_units,
                BEACON_RANGER_GUARD_OFFSETS,
                rotation=turn.tick // BEACON_GUARD_PATROL_TICKS,
                evenly_spaced=True,
            )
        for ranger in ordered:
            if ranger.id in acted_units:
                continue
            if ranger.id in home_reserve_rangers:
                home_shots = [
                    (enemy, cell)
                    for enemy, cell in self._ranger_shot_candidates(
                        turn,
                        ranger,
                        planner,
                    )
                    if not isinstance(enemy, CoreView)
                    and turn.core is not None
                    and _distance(enemy.position, turn.core.position)
                    <= AGGRESS_CORE_ALERT_RADIUS
                ]
                if home_shots:
                    target, cell = min(
                        home_shots,
                        key=lambda pair: (
                            _enemy_role_priority(pair[0]),
                            _effective_hp(pair[0]),
                            pair[0].id.bytes,
                        ),
                    )
                    ranger.shoot(target, expected_cell=cell)
                    self._mark_ranger_shot(target, cell)
                    decisions.append(
                        f"ranger:{_short_id(ranger.id)} shoot "
                        f"target={_short_id(target.id)} expected={cell} "
                        "role=home_reserve_defend"
                    )
                    self.memory.decision_totals["ranger:home_reserve_defend"] += 1
                    continue
                patrol_slot = patrol_slots.get(ranger.id)
                if patrol_slot is not None and ranger.position != patrol_slot:
                    planner.toward(
                        ranger,
                        patrol_slot,
                        "aggress_core_contract"
                        if core_alert
                        else "aggress_core_watch",
                    )
                self.memory.decision_totals["ranger:aggress_guard"] += 1
                continue
            if (
                core_target is not None
                and ranger.id in core_assault_rangers
                and not core_assault_ready
                and core_assault_rally is not None
            ):
                planner.toward(ranger, core_assault_rally, "enemy_core_rally")
                decisions.append(
                    f"ranger:{_short_id(ranger.id)} enemy_core_rally "
                    f"target={core_target} rally={core_assault_rally}"
                )
                self.memory.decision_totals["ranger:enemy_core_rally"] += 1
                continue
            if (
                core_target is not None
                and core_assault_ready
                and ranger.id in core_assault_rangers
                and ranger.id not in beacon_ranger_guard_ids
            ):
                range_three_shot = (
                    max(
                        abs(ranger.position[0] - core_target[0]),
                        abs(ranger.position[1] - core_target[1]),
                    )
                    == 3
                    and _is_legal_ranger_shot(
                        ranger.position,
                        core_target,
                        planner.obstacles,
                    )
                )
                if range_three_shot:
                    # The coordinate is already confirmed by the assault
                    # order.  Cell fire remains legal even if the diagonal
                    # range-three position lies just outside Core vision.
                    ranger.shoot_cell(core_target)
                    decisions.append(
                        f"ranger:{_short_id(ranger.id)} shoot_cell "
                        f"target={core_target} role=enemy_core_range3"
                    )
                    self.memory.decision_totals[
                        "ranger:enemy_core_range3_cell_fire"
                    ] += 1
                    continue
                core_shots = [
                    (enemy, cell)
                    for enemy, cell in self._ranger_shot_candidates(
                        turn,
                        ranger,
                        planner,
                    )
                    if isinstance(enemy, CoreView)
                    and enemy.position == core_target
                ]
                if core_shots:
                    target, cell = min(
                        core_shots,
                        key=lambda pair: (
                            1
                            if assigned_damage[pair[0].id]
                            >= _effective_hp(pair[0])
                            else 0,
                            pair[0].id.bytes,
                        ),
                    )
                    ranger.shoot(target, expected_cell=cell)
                    self._mark_ranger_shot(target, cell)
                    assigned_damage[target.id] += 1
                    decisions.append(
                        f"ranger:{_short_id(ranger.id)} shoot "
                        f"target={_short_id(target.id)} expected={cell} "
                        "role=enemy_core_priority"
                    )
                    self.memory.decision_totals[
                        "ranger:enemy_core_priority_shoot"
                    ] += 1
                else:
                    target = self._core_assault_ranger_position(
                        ranger,
                        core_target,
                        planner,
                    ) or core_target
                    planner.toward(
                        ranger,
                        target,
                        "enemy_core_seek_firing",
                    )
                    decisions.append(
                        f"ranger:{_short_id(ranger.id)} enemy_core_seek_firing "
                        f"target={core_target} firing={target}"
                    )
                    self.memory.decision_totals[
                        "ranger:enemy_core_assault"
                    ] += 1
                continue
            if ranger.id in beacon_ranger_guard_ids and carrier is not None:
                shot_candidates = [
                    (enemy, cell)
                    for enemy, cell in self._ranger_shot_candidates(
                        turn,
                        ranger,
                        planner,
                    )
                    if _distance(enemy.position, carrier.position)
                    <= BEACON_GUARD_THREAT_RADIUS
                ]
                if shot_candidates:
                    target, cell = min(
                        shot_candidates,
                        key=lambda pair: (
                            1
                            if assigned_damage[pair[0].id]
                            >= _effective_hp(pair[0])
                            else 0,
                            0 if isinstance(pair[0], CoreView) else 1,
                            _enemy_role_priority(pair[0]),
                            _effective_hp(pair[0]),
                            pair[0].id.bytes,
                        ),
                    )
                    ranger.shoot(target, expected_cell=cell)
                    self._mark_ranger_shot(target, cell)
                    assigned_damage[target.id] += 1
                    decisions.append(
                        f"ranger:{_short_id(ranger.id)} shoot "
                        f"target={_short_id(target.id)} expected={cell} "
                        "role=beacon_guard"
                    )
                    self.memory.decision_totals["beacon_guard:ranger_shoot"] += 1
                    continue
                if (
                    beacon_guard_threats
                    and ranger.id == beacon_ranger_interceptor_id
                ):
                    threat = min(
                        beacon_guard_threats,
                        key=lambda enemy: (
                            _enemy_role_priority(enemy),
                            _distance(ranger.position, enemy.position),
                            enemy.id.bytes,
                        ),
                    )
                    firing_cells = self._firing_cells(
                        threat.position,
                        planner.obstacles,
                    )
                    if firing_cells:
                        firing_cell = min(
                            firing_cells,
                            key=lambda position: (
                                planner.threat.get(position, 0),
                                _distance(ranger.position, position),
                                position,
                            ),
                        )
                        planner.toward(
                            ranger,
                            firing_cell,
                            "beacon_ranger_intercept",
                        )
                    else:
                        planner.toward(
                            ranger,
                            carrier.position,
                            "beacon_ranger_intercept",
                        )
                    self.memory.decision_totals["beacon_guard:ranger_intercept"] += 1
                    continue
                slot = beacon_guard_slots.get(ranger.id, carrier.position)
                if ranger.position != slot:
                    planner.toward(
                        ranger,
                        slot,
                        "beacon_ranger_guard_patrol",
                    )
                else:
                    ranger.wait()
                self.memory.decision_totals["beacon_guard:ranger_patrol"] += 1
                continue
            if (
                reinforcement_active
                and turn.core is not None
                and ranger.id not in defender_ids
            ):
                threat_ids = {enemy.id for enemy in reinforcement_threats}
                reinforcement_shots = [
                    (enemy, cell)
                    for enemy, cell in self._ranger_shot_candidates(
                        turn,
                        ranger,
                        planner,
                    )
                    if enemy.id in threat_ids
                ]
                if reinforcement_shots:
                    target, cell = min(
                        reinforcement_shots,
                        key=lambda pair: (
                            1
                            if assigned_damage[pair[0].id]
                            >= _effective_hp(pair[0])
                            else 0,
                            _enemy_role_priority(pair[0]),
                            _effective_hp(pair[0]),
                            pair[0].id.bytes,
                        ),
                    )
                    ranger.shoot(target, expected_cell=cell)
                    self._mark_ranger_shot(target, cell)
                    assigned_damage[target.id] += 1
                    decisions.append(
                        f"ranger:{_short_id(ranger.id)} shoot "
                        f"target={_short_id(target.id)} expected={cell} "
                        "role=core_reinforce"
                    )
                    self.memory.decision_totals[
                        "core_reinforcement:ranger_shoot"
                    ] += 1
                    continue
                if reinforcement_threats:
                    threat = min(
                        reinforcement_threats,
                        key=lambda enemy: (
                            _distance(enemy.position, turn.core.position),
                            _enemy_role_priority(enemy),
                            _distance(ranger.position, enemy.position),
                            enemy.id.bytes,
                        ),
                    )
                    firing_cells = {
                        position
                        for position in self._firing_cells(
                            threat.position,
                            planner.obstacles,
                        )
                        if _distance(position, turn.core.position)
                        <= AGGRESS_CORE_ALERT_RADIUS
                    }
                    target = (
                        min(
                            firing_cells,
                            key=lambda position: (
                                planner.threat.get(position, 0),
                                _distance(ranger.position, position),
                                position,
                            ),
                        )
                        if firing_cells
                        else turn.core.position
                    )
                else:
                    attackers = [
                        unit
                        for unit in ordered
                        if unit.id not in defender_ids
                        and unit.id not in beacon_ranger_guard_ids
                    ]
                    offset = RANGER_RECALL_OFFSETS[
                        attackers.index(ranger) % len(RANGER_RECALL_OFFSETS)
                    ]
                    target = (
                        turn.core.position[0] + offset[0],
                        turn.core.position[1] + offset[1],
                    )
                if ranger.position != target:
                    planner.toward(
                        ranger,
                        target,
                        "aggress_core_reinforce",
                    )
                else:
                    ranger.wait()
                decisions.append(
                    f"ranger:{_short_id(ranger.id)} core_reinforce "
                    f"target={target}"
                )
                self.memory.decision_totals[
                    "core_reinforcement:ranger_return"
                ] += 1
                continue
            ranger_key = str(ranger.id)
            # 0. 被攻击且被近身 → 先撤退回走位（不原地挨打）
            if (
                ranger_key in self.memory.attacked_units
                and now - self.memory.attacked_units[ranger_key] <= 30
                and turn.visible_enemies
            ):
                nearest_enemy = min(
                    turn.visible_enemies,
                    key=lambda enemy: _distance(enemy.position, ranger.position),
                )
                if _distance(nearest_enemy.position, ranger.position) <= 3:
                    retreat = (
                        ranger.position[0] * 2 - nearest_enemy.position[0],
                        ranger.position[1] * 2 - nearest_enemy.position[1],
                    )
                    planner.toward(ranger, retreat, "aggress_retreat")
                    decisions.append(
                        f"ranger:{_short_id(ranger.id)} retreat "
                        f"from={_short_id(nearest_enemy.id)}"
                    )
                    self.memory.decision_totals["ranger:retreat"] += 1
                    continue
            shot_candidates = self._ranger_shot_candidates(turn, ranger, planner)
            if ranger.id in defender_ids:
                shot_candidates = [
                    (enemy, cell)
                    for enemy, cell in shot_candidates
                    if not isinstance(enemy, CoreView)
                    if (
                        turn.core is None
                        or _distance(enemy.position, turn.core.position)
                        <= RANGER_DEFENSE_LEASH_RADIUS
                    )
                ]
            if shot_candidates:
                target, cell = min(
                    shot_candidates,
                    key=lambda pair: (
                        1 if assigned_damage[pair[0].id] >= _effective_hp(pair[0]) else 0,
                        0 if isinstance(pair[0], CoreView) else 1,
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
                    f"expected={cell} role=aggress"
                )
                self.memory.decision_totals["ranger:shoot"] += 1
                continue
            # 1. 守家单位：安全时展开视野，预警后回到紧凑火力圈。
            if ranger.id in defender_ids:
                patrol_slot = patrol_slots.get(ranger.id)
                if patrol_slot is not None and ranger.position != patrol_slot:
                    planner.toward(
                        ranger,
                        patrol_slot,
                        (
                            "aggress_core_contract"
                            if core_alert
                            else "aggress_core_watch"
                        ),
                    )
                elif (
                    core_alert
                    and patrol_slot is None
                    and turn.core is not None
                    and _distance(ranger.position, turn.core.position) > 2
                ):
                    planner.toward(ranger, turn.core.position, "aggress_core_guard")
                self.memory.decision_totals["ranger:aggress_guard"] += 1
                continue
            # 2. 支援被攻击的队友：向受害者推进到射程
            if attacked_victims and not core_priority_active:
                nearest_victim = min(
                    attacked_victims,
                    key=lambda position: _distance(ranger.position, position),
                )
                victim_distance = _distance(ranger.position, nearest_victim)
                if victim_distance > 3:
                    firing_cells = self._firing_cells(
                        nearest_victim, planner.obstacles
                    )
                    if firing_cells:
                        firing_cell = min(
                            firing_cells,
                            key=lambda position: (
                                planner.threat.get(position, 0),
                                _distance(ranger.position, position),
                                position,
                            ),
                        )
                        planner.toward(ranger, firing_cell, "aggress_support_firing")
                    else:
                        planner.toward(ranger, nearest_victim, "aggress_support")
                    self.memory.decision_totals["ranger:support"] += 1
                    continue
            # 移动：向敌人（Core 优先）推进到射程内
            if combat_target is not None:
                firing_cells = self._firing_cells(combat_target, planner.obstacles)
                if firing_cells:
                    firing_cell = min(
                        firing_cells,
                        key=lambda position: (
                            planner.threat.get(position, 0),
                            _distance(ranger.position, position),
                            position,
                        ),
                    )
                    planner.toward(ranger, firing_cell, "aggress_seek_firing")
                else:
                    planner.toward(ranger, combat_target, "aggress_approach")
                self.memory.decision_totals["ranger:assault"] += 1
                continue
            if frontier_target is not None:
                # 编队散布：不同游侠分散到信标方向前沿不同方位，避免全队挤一个点
                spread = SPREAD_OFFSETS[frontier_probe_count % len(SPREAD_OFFSETS)]
                spread_cell = (
                    frontier_target[0] + spread[0],
                    frontier_target[1] + spread[1],
                )
                planner.toward(ranger, spread_cell, "aggress_frontier")
                frontier_probe_count += 1
                self.memory.decision_totals["ranger:frontier"] += 1

    def _choose_rangers_beacon(
        self,
        turn: Turn,
        planner: MovementPlanner,
        acted_units: set[UUID],
        decisions: list[str],
    ) -> None:
        assigned_damage: Counter[UUID] = Counter()
        beacon_position = turn.beacon.position
        home_vanguards, home_rangers = self._beacon_home_reserve_ids(turn)
        (
            local_core_target,
            local_sortie_vanguards,
            local_sortie_rangers,
        ) = self._beacon_local_core_sortie_assignments(
            turn,
            home_vanguards,
            home_rangers,
            decisions,
        )
        protected_vanguards = home_vanguards | local_sortie_vanguards
        protected_rangers = home_rangers | local_sortie_rangers
        if local_core_target is not None:
            self._choose_beacon_local_core_sortie_rangers(
                turn,
                planner,
                acted_units,
                decisions,
                local_core_target,
                local_sortie_rangers,
            )
        core_target = self._beacon_core_assault_target(
            turn,
            protected_vanguards,
            protected_rangers,
        )
        strategic_target = core_target or beacon_position
        order = self._beacon_expedition_order(
            turn,
            planner,
            protected_vanguards,
            protected_rangers,
            strategic_target,
            core_target=core_target,
            excluded_ids=acted_units,
        )
        expedition_rangers = [
            unit
            for unit in turn.rangers
            if unit.id not in protected_rangers and unit.id not in acted_units
        ]
        formation_slots = self._beacon_guard_slots(
            turn,
            planner,
            order.formation_anchor,
            expedition_rangers,
            BEACON_EXPEDITION_RANGER_OFFSETS,
            evenly_spaced=True,
        )
        for ranger in sorted(turn.rangers, key=_uuid_key):
            if ranger.id in acted_units or ranger.id in protected_rangers:
                continue
            if (
                order.phase in BEACON_EXPEDITION_FORMATION_PRIORITY_PHASES
                and self._move_beacon_expedition_unit(
                    turn,
                    planner,
                    ranger,
                    order,
                    formation_slots,
                    decisions,
                )
            ):
                continue
            # 优先射信标附近的敌人
            all_shot_candidates = self._ranger_shot_candidates(
                turn,
                ranger,
                planner,
            )
            if core_target is not None:
                all_shot_candidates = [
                    (enemy, cell)
                    for enemy, cell in all_shot_candidates
                    if not (
                        isinstance(enemy, UnitView)
                        and enemy.unit_type is UnitType.WORKER
                    )
                ]
            core_shots = [
                (enemy, cell)
                for enemy, cell in all_shot_candidates
                if core_target is not None
                and isinstance(enemy, CoreView)
                and enemy.position == core_target
            ]
            if core_target is not None and order.phase == "core_focus" and not core_shots:
                target = self._core_assault_ranger_position(
                    ranger,
                    core_target,
                    planner,
                )
                if target is not None:
                    planner.toward(ranger, target, "beacon_core_focus")
                    decisions.append(
                        f"ranger:{_short_id(ranger.id)} core_focus "
                        f"target={core_target} firing={target}"
                    )
                    self.memory.decision_totals["ranger:beacon_core_focus"] += 1
                    continue
            shot_candidates = core_shots or [
                (enemy, cell)
                for enemy, cell in all_shot_candidates
                if _distance(enemy.position, beacon_position) <= 5
            ]
            if not shot_candidates:
                shot_candidates = all_shot_candidates
            if shot_candidates:
                target, cell = min(
                    shot_candidates,
                    key=lambda pair: (
                        1 if assigned_damage[pair[0].id] >= _effective_hp(pair[0]) else 0,
                        0 if isinstance(pair[0], CoreView) else 1,
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
                    f"expected={cell} role=beacon"
                )
                self.memory.decision_totals["ranger:shoot"] += 1
                continue
            if (
                core_target is not None
                and order.phase == "weak_core_strike"
                and ranger.id in order.assault_ids
            ):
                target = self._core_assault_ranger_position(
                    ranger,
                    core_target,
                    planner,
                ) or core_target
                planner.toward(ranger, target, "enemy_core_seek_firing")
                decisions.append(
                    f"ranger:{_short_id(ranger.id)} enemy_core_seek_firing "
                    f"target={core_target} firing={target} role=beacon_surplus"
                )
                self.memory.decision_totals[
                    "ranger:enemy_core_assault"
                ] += 1
                continue
            self._move_beacon_expedition_unit(
                turn,
                planner,
                ranger,
                order,
                formation_slots,
                decisions,
            )
        self._choose_rangers_defend(
            turn,
            planner,
            acted_units,
            decisions,
            eligible_ids=home_rangers - local_sortie_rangers,
        )

    def _choose_rangers_develop(
        self,
        turn: Turn,
        planner: MovementPlanner,
        acted_units: set[UUID],
        decisions: list[str],
    ) -> None:
        _, scout_rangers = self._develop_beacon_scout_ids(turn)
        assigned_damage: Counter[UUID] = Counter()
        for ranger in sorted(turn.rangers, key=_uuid_key):
            if ranger.id not in scout_rangers or ranger.id in acted_units:
                continue
            shot_candidates = self._ranger_shot_candidates(turn, ranger, planner)
            if shot_candidates:
                target, cell = min(
                    shot_candidates,
                    key=lambda pair: (
                        1 if assigned_damage[pair[0].id] >= _effective_hp(pair[0]) else 0,
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
                    f"expected={cell} role=develop_beacon_scout"
                )
                self.memory.decision_totals["ranger:shoot"] += 1
                continue
            if planner.toward(
                ranger,
                turn.beacon.position,
                "develop_beacon_ranger",
            ):
                decisions.append(
                    f"ranger:{_short_id(ranger.id)} beacon_head_start "
                    f"target={turn.beacon.position}"
                )
                self.memory.decision_totals["beacon:early_ranger_advance"] += 1

        home_rangers = {
            unit.id for unit in turn.rangers if unit.id not in scout_rangers
        }
        self._choose_rangers_defend(
            turn,
            planner,
            acted_units,
            decisions,
            eligible_ids=home_rangers,
        )

    def _choose_rangers_recall(
        self,
        turn: Turn,
        planner: MovementPlanner,
        acted_units: set[UUID],
        decisions: list[str],
        excluded_ids: set[UUID] | None = None,
    ) -> None:
        assigned_damage: Counter[UUID] = Counter()
        excluded = excluded_ids or set()
        ordered_rangers = sorted(
            (unit for unit in turn.rangers if unit.id not in excluded),
            key=lambda ranger: (
                self.memory.unit_labels.get(
                    str(ranger.id),
                    UnitLabel(UnitType.RANGER.value, 1_000_000),
                ).number,
                ranger.id.bytes,
            ),
        )
        recall_offsets = _terrain_guard_offsets(
            turn.core.position if turn.core is not None else (0, 0),
            planner.obstacles,
            RANGER_RECALL_OFFSETS,
        )
        patrol_rangers = ordered_rangers[: min(CORE_PATROL_RANGER_COUNT * 2, len(ordered_rangers))]
        patrol_slots = self._core_patrol_slots(turn, planner, patrol_rangers)
        for ranger in ordered_rangers:
            if ranger.id in acted_units:
                continue
            shot_candidates = [
                (enemy, cell)
                for enemy, cell in self._ranger_shot_candidates(turn, ranger, planner)
                if (
                    turn.core is None
                    or _distance(enemy.position, turn.core.position) <= 6
                )
            ]
            if shot_candidates:
                target, cell = min(
                    shot_candidates,
                    key=lambda pair: (
                        1 if assigned_damage[pair[0].id] >= _effective_hp(pair[0]) else 0,
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
                    f"expected={cell} role=recall"
                )
                self.memory.decision_totals["ranger:shoot"] += 1
                continue
            patrol_slot = patrol_slots.get(ranger.id)
            if patrol_slot is not None and ranger.position != patrol_slot:
                if planner.toward(ranger, patrol_slot, "ranger_recall_patrol"):
                    self.memory.decision_totals["ranger:recall"] += 1
                    continue
            if turn.core is not None and _distance(ranger.position, turn.core.position) > 2:
                offset = recall_offsets[
                    ordered_rangers.index(ranger) % len(recall_offsets)
                ]
                target = (
                    turn.core.position[0] + offset[0],
                    turn.core.position[1] + offset[1],
                )
                planner.toward(ranger, target, "ranger_recall_core")
                self.memory.decision_totals["ranger:recall"] += 1

    def _choose_rangers_migrate(
        self,
        turn: Turn,
        planner: MovementPlanner,
        acted_units: set[UUID],
        decisions: list[str],
    ) -> None:
        carrier, _, ranger_guard_ids = self._aggress_beacon_guard_assignments(turn)
        protected_ids = set(ranger_guard_ids)
        if carrier is not None:
            guards = [unit for unit in turn.rangers if unit.id in ranger_guard_ids]
            slots = self._beacon_guard_slots(
                turn,
                planner,
                carrier.position,
                guards,
                BEACON_RANGER_GUARD_OFFSETS,
            )
            for guard in guards:
                if guard.id in acted_units:
                    continue
                candidates = [
                    (enemy, cell)
                    for enemy, cell in self._ranger_shot_candidates(
                        turn,
                        guard,
                        planner,
                    )
                    if _distance(enemy.position, carrier.position)
                    <= BEACON_GUARD_THREAT_RADIUS
                ]
                if candidates:
                    target, cell = min(
                        candidates,
                        key=lambda pair: (
                            _enemy_role_priority(pair[0]),
                            _distance(guard.position, pair[0].position),
                            pair[0].id.bytes,
                        ),
                    )
                    guard.shoot(target, expected_cell=cell)
                    self._mark_ranger_shot(target, cell)
                    acted_units.add(guard.id)
                    self.memory.decision_totals["ranger:shoot"] += 1
                    continue
                slot = slots.get(guard.id, carrier.position)
                if guard.position == slot:
                    guard.wait()
                    acted_units.add(guard.id)
                elif planner.toward(guard, slot, "migration_beacon_ranger_guard"):
                    acted_units.add(guard.id)

        self._choose_rangers_recall(
            turn,
            planner,
            acted_units,
            decisions,
            excluded_ids=protected_ids,
        )

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

    def _choose_vanguards_lightning(
        self,
        turn: Turn,
        planner: MovementPlanner,
        acted_units: set[UUID],
        decisions: list[str],
    ) -> None:
        """闪电模式先锋：近行星轨道绕 Core 转圈护卫，猎杀无护卫敌方 Core。

        轨迹改造（绕银河体系）：先锋是 Core 的"近行星"——绕 core.position 转方环，
        不再 V 字外插。无猎杀/无近中环警报时全体在近轨 patrol（半径
        LIGHTNING_NEAR_ORBIT_RADIUS=5，贴 Core 视野边缘转、增加保护力）；多先锋按 UUID 序
        错开 phase 实现第一/第三象限对位。仅 NEAR/MID 勤王或猎杀可打目标时离轨。
        """
        if turn.core is None:
            return
        # 回防分级：NEAR/MID 才全员回防；FAR 仅局部(由游侠处理),先锋照常近轨。
        tier = self._lightning_defense_tier(turn)
        if tier in ("NEAR", "MID"):
            self._choose_vanguards_recall(turn, planner, acted_units, decisions)
            return
        for vanguard in sorted(turn.vanguards, key=_uuid_key):
            if vanguard.id in acted_units:
                continue
            uid = str(vanguard.id)
            core_id = self._lightning_claim_for(uid)
            target_position: Position | None = None
            visible_core: CoreView | None = None
            if core_id is not None:
                target_position, visible_core = self._lightning_target_position(
                    turn, core_id
                )
                if self._lightning_target_attended(turn, target_position) or (
                    visible_core is None
                    and self._lightning_target_crowded(target_position)
                ):
                    self._lightning_blacklist_core(core_id)
                    core_id = None
                    target_position = None
                    visible_core = None
            if core_id is None:
                core_id = self._lightning_acquire_target(turn, vanguard)
                if core_id is not None:
                    target_position, visible_core = self._lightning_target_position(
                        turn, core_id
                    )
            if core_id is not None and target_position is not None:
                # 兵种细分：先锋近战。SKIP(有游侠守卫) → 拉黑回避;
                # CHICKEN(无护卫)与 PRESS(只先锋守卫) → 先锋照常进(近战对先锋公平,
                #   guard_cells 把守卫格当障碍让先锋侧面包抄,即"绕开 distant guard
                #   保留 claim"的旧行为)。先锋不掺和游击——那是游侠的事。
                assessment = self._lightning_engage_assessment(turn, target_position)
                if assessment == "SKIP":
                    self._lightning_blacklist_core(core_id)
                    core_id = None
                    target_position = None
                    visible_core = None
            if core_id is not None and target_position is not None:
                direction = next(
                    (
                        candidate
                        for candidate in DIRECTION_ORDER
                        if _destination(vanguard.position, candidate)
                        == target_position
                    ),
                    None,
                )
                if visible_core is not None and direction is not None:
                    vanguard.sweep(direction)
                    decisions.append(
                        f"lightning:{_short_id(vanguard.id)} sweep "
                        f"target={target_position}"
                    )
                    self.memory.decision_totals["lightning:vanguard_sweep"] += 1
                else:
                    # 朝目标走。守卫格在威胁图里已标记，step_toward 会自然绕开。
                    # 不再用 planner.toward(A*)，改用 _lightning_step_toward 防横跳。
                    if not self._lightning_step_toward(
                        turn, planner, vanguard, target_position, "lightning_hunt"
                    ):
                        vanguard.wait()
                acted_units.add(vanguard.id)
                continue
            # 无猎杀/不进猎杀 → 绕 Core 近行星轨道转圈护卫（不走 A*，Core 风格四邻打分）。
            # 局部威胁检测：先锋周围有敌方战斗单位且无猎杀目标时，撤向 Core 避战。
            if self._lightning_has_local_threat(turn, vanguard):
                retreat_target = turn.core.position
                if not self._lightning_step_toward(
                    turn, planner, vanguard, retreat_target, "lightning_retreat_local_threat"
                ):
                    vanguard.wait()
                acted_units.add(vanguard.id)
                continue
            orbit = self._lightning_orbit_waypoint(turn, vanguard, UnitType.VANGUARD)
            if orbit is not None and not self._lightning_step_toward(
                turn, planner, vanguard, orbit, "lightning_vanguard_orbit"
            ):
                vanguard.wait()
            acted_units.add(vanguard.id)

    def _choose_rangers_lightning(
        self,
        turn: Turn,
        planner: MovementPlanner,
        acted_units: set[UUID],
        decisions: list[str],
    ) -> None:
        """闪电模式游侠：四层轨道职责 + 分层防御。

        四层轨道职责：
        - 开路轨道（4游侠，绕原点）：探索资源 + 侦察无守卫Core + 选择性交战
          - 只有1v1先锋时游击（利用射程优势）
          - 见游侠/多敌立即绕路
          - 不能离开开路轨道范围
        - 近轨道（先锋，r=5）：绝对不离开，守卫Core内层
        - 中轨道（剩余游侠）：正常巡逻 + 分层应敌
        - 远轨道（工人）：可离开采集，防御时回近轨道当肉盾

        分层防御（按敌方深入程度）：
        - 敌入远轨道 → 狙击驱离（不贴脸，最远追到外轨道边界）
        - 敌入中轨道 → 集结所有中轨游侠围攻，工人回近轨道当肉盾
        - 敌入近轨道 → 游侠退入工人包围圈阻击 + 召回开路游侠勤王（沿途绕过敌人）

        总原则：
        - 非必要不进攻（除非压倒性优势）
        - 资源靠采集，不掠夺
        - 禁止千里追击
        """
        if turn.core is None:
            return

        # Step 1: 威胁分级
        tier = self._lightning_defense_tier(turn)

        # Step 2: NEAR威胁 → 所有游侠回防（召回开路游侠勤王）
        if tier == "NEAR":
            for ranger in sorted(turn.rangers, key=_uuid_key):
                if ranger.id in acted_units:
                    continue
                # 游侠退入工人包围圈阻击（近轨道r=5附近）
                retreat_target = turn.core.position
                if not self._lightning_step_toward(
                    turn, planner, ranger, retreat_target, "lightning_defend_NEAR"
                ):
                    ranger.wait()
                decisions.append(
                    f"ranger:{_short_id(ranger.id)} defend_NEAR retreat_to_core"
                )
                self.memory.decision_totals["ranger:defend_NEAR"] += 1
                acted_units.add(ranger.id)
            return

        # Step 3: MID威胁 → 集结所有中轨游侠围攻
        if tier == "MID":
            nearest_threat = self._lightning_find_nearest_threat(turn)
            if nearest_threat is None:
                # 找不到威胁，降级为正常巡逻
                tier = "NONE"
            else:
                for ranger in sorted(turn.rangers, key=_uuid_key):
                    if ranger.id in acted_units:
                        continue
                    # 所有游侠集结到威胁位置，保持射程（2-3）狙击
                    intercept_pos = self._lightning_intercept_position(
                        turn, ranger, nearest_threat
                    )
                    if not self._lightning_step_toward(
                        turn, planner, ranger, intercept_pos, "lightning_defend_MID"
                    ):
                        ranger.wait()
                    decisions.append(
                        f"ranger:{_short_id(ranger.id)} defend_MID intercept "
                        f"threat={nearest_threat.position}"
                    )
                    self.memory.decision_totals["ranger:defend_MID"] += 1
                    acted_units.add(ranger.id)
                return

        # Step 4: FAR威胁或无威胁 → 按职责分工
        ordered_rangers = sorted(turn.rangers, key=_uuid_key)
        core_origin_dist = _distance(turn.core.position, (0, 0))
        breakthrough_safe = core_origin_dist <= LIGHTNING_BREAKTHROUGH_MAX_CORE_DISTANCE

        for index, ranger in enumerate(ordered_rangers):
            if ranger.id in acted_units:
                continue

            # 开路游侠（前4个）
            if index < LIGHTNING_BREAKTHROUGH_SLOT_COUNT and breakthrough_safe:
                # Phase 3: 开路游侠战术 - 威胁检测优先
                action, target = self._lightning_breakthrough_threat_check(turn, ranger)

                if action == "flee":
                    # 见游侠/多敌 → 逃向Core（沿途绕过敌人）
                    if target and not self._lightning_step_toward(
                        turn, planner, ranger, target, "breakthrough_flee"
                    ):
                        ranger.wait()
                    decisions.append(
                        f"ranger:{_short_id(ranger.id)} breakthrough_flee to_core"
                    )
                    self.memory.decision_totals["breakthrough:flee"] += 1
                    acted_units.add(ranger.id)
                    continue

                elif action == "kite":
                    # 1v1先锋 → 游击（保持2-3格射程优势）
                    if target and not self._lightning_step_toward(
                        turn, planner, ranger, target, "breakthrough_kite"
                    ):
                        ranger.wait()
                    decisions.append(
                        f"ranger:{_short_id(ranger.id)} breakthrough_kite vs_vanguard"
                    )
                    self.memory.decision_totals["breakthrough:kite"] += 1
                    acted_units.add(ranger.id)
                    continue

                # action == "patrol" → 继续巡逻（可能发现无守卫Core）
                # 搜索附近无守卫Core（选择性交战）
                unguarded_core = self._lightning_find_nearby_unguarded_core(turn, ranger)
                if unguarded_core is not None:
                    # 判定是否应交战
                    should_engage = self._lightning_should_breakthrough_engage(
                        turn, ranger, unguarded_core
                    )
                    if should_engage:
                        # 可以打 → 朝目标移动或射击
                        target_pos = unguarded_core.position
                        shots = [
                            (enemy, cell)
                            for enemy, cell in self._ranger_shot_candidates(
                                turn, ranger, planner
                            )
                            if isinstance(enemy, CoreView)
                            and enemy.id == unguarded_core.id
                        ]
                        if shots:
                            enemy, cell = min(shots, key=lambda pair: pair[1])
                            ranger.shoot(enemy, expected_cell=cell)
                            self._mark_ranger_shot(enemy, cell)
                            decisions.append(
                                f"breakthrough:{_short_id(ranger.id)} shoot_unguarded_core"
                            )
                            self.memory.decision_totals["breakthrough:shoot"] += 1
                        else:
                            # 朝目标移动
                            if not self._lightning_step_toward(
                                turn, planner, ranger, target_pos, "breakthrough_approach"
                            ):
                                ranger.wait()
                            decisions.append(
                                f"breakthrough:{_short_id(ranger.id)} approach_unguarded"
                            )
                            self.memory.decision_totals["breakthrough:approach"] += 1
                        acted_units.add(ranger.id)
                        continue

                # 无可打目标 → 继续开路轨道巡逻
                scout = self._lightning_breakthrough_target(turn, ranger, index)
                if scout and not self._lightning_step_toward(
                    turn, planner, ranger, scout, "breakthrough_patrol"
                ):
                    ranger.wait()
                decisions.append(
                    f"breakthrough:{_short_id(ranger.id)} patrol"
                )
                self.memory.decision_totals["breakthrough:patrol"] += 1
                acted_units.add(ranger.id)
                continue

            # 中轨游侠（第5+个）
            else:
                # FAR威胁 → 就近游侠狙击驱离（不贴脸，最远追到外轨道边界）
                if tier == "FAR":
                    nearest_threat = self._lightning_find_nearest_threat(turn)
                    if nearest_threat:
                        # 检查该游侠是否靠近威胁（视野范围内）
                        dist_to_threat = _distance(ranger.position, nearest_threat.position)
                        ranger_vision = 5  # 游侠视野半径

                        if dist_to_threat <= ranger_vision * 2:  # 视野范围内才参与狙击
                            # 保持射程（2-3）狙击
                            kite_pos = self._lightning_kiting_position(
                                turn, ranger, nearest_threat
                            )
                            if not self._lightning_step_toward(
                                turn, planner, ranger, kite_pos, "mid_orbit_snipe_FAR"
                            ):
                                ranger.wait()
                            decisions.append(
                                f"ranger:{_short_id(ranger.id)} mid_orbit_snipe_FAR "
                                f"threat_dist={dist_to_threat}"
                            )
                            self.memory.decision_totals["mid_orbit:snipe_FAR"] += 1
                            acted_units.add(ranger.id)
                            continue

                # 无威胁或不在狙击范围 → 正常中轨巡逻
                if index < LIGHTNING_BREAKTHROUGH_SLOT_COUNT:
                    mid_lane = index  # 原本应开路但因距离禁用
                else:
                    mid_lane = index - LIGHTNING_BREAKTHROUGH_SLOT_COUNT

                scout = self._lightning_orbit_waypoint(
                    turn, ranger, UnitType.RANGER, lane=mid_lane
                )
                if scout and not self._lightning_step_toward(
                    turn, planner, ranger, scout, "mid_orbit_patrol"
                ):
                    ranger.wait()
                decisions.append(
                    f"ranger:{_short_id(ranger.id)} mid_orbit_patrol lane={mid_lane}"
                )
                self.memory.decision_totals["mid_orbit:patrol"] += 1
                acted_units.add(ranger.id)


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

    def _lightning_build_slot(self, current_population: int) -> UnitType | None:
        """固定产兵阶梯第 current_population 槽(0-indexed,即"再造一个就是第几个")
        该造的兵种。前 8 槽按 LIGHTNING_BUILD_ORDER,第 9 槽起全游侠,满 20 返回 None。

        索引 = current_population - 1：游戏起手送 1 免费工人(pop1),所以"第 1 个造的
        兵"是在 pop1 时造,对应槽 0。容量 max(10,pop*5) 自然走通:
        pop1 cap10≥先锋10 → pop2; pop2 cap10≥工人5 → pop3; pop3 cap15≥游侠12。
        """
        if current_population >= LIGHTNING_MAX_POPULATION:
            return None
        slot = max(0, current_population - 1)
        if slot < len(LIGHTNING_BUILD_ORDER):
            return LIGHTNING_BUILD_ORDER[slot]
        return UnitType.RANGER

    def _select_spawn(
        self,
        turn: Turn,
        projected_resources: int,
    ) -> UnitType | None:
        """Return the Unit this Core would produce if its cell had capacity."""
        core = turn.core
        if core is None:
            return None

        current_population = len(turn.units)
        worker_cost = unit_cost(UnitType.WORKER, current_population)
        vanguard_cost = unit_cost(UnitType.VANGUARD, current_population)
        ranger_cost = unit_cost(UnitType.RANGER, current_population)

        # === 资源容量紧急管理：资源达到容量 80% 时优先造工人消耗资源 ===
        # 触发条件：未达 100 人总上限 + 资源≥容量*0.8 + 买得起工人
        # 优先级高于固定产兵阶梯，避免资源溢出浪费。20-100 人区间纯工人，
        # 不影响 20 人内战斗配置（游侠/先锋比例照旧）。
        capacity = turn.resource_capacity
        urgency_threshold = int(capacity * 0.8)
        if (
            current_population < ABSOLUTE_MAX_POPULATION
            and projected_resources >= urgency_threshold
            and projected_resources >= worker_cost
            and current_population >= LIGHTNING_MAX_POPULATION  # 只在超过 20 人后才触发紧急工人
        ):
            return UnitType.WORKER

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
        reserve = 2 if near_threat or core.shield < shield_cap else 0
        budget = projected_resources - reserve

        # 闪电模式固定产兵阶梯（用户指定，攒钱优先不 fallthrough）：
        #   pop1→先锋, 2→工人, 3→游侠, 4→工人, 5→游侠, 6→工人,
        #   7→游侠, 8→工人, 9+→游侠, 满 20 停。
        # 只造 1 先锋(先锋不强,前期一个够;肉盾由工人充当,勤王时游侠躲工人
        # 后面狙击)。pop≥9 起只造游侠。容量 max(10,pop*5) 自然走通:
        # pop1 cap10≥先锋10; pop2 cap10≥工人5; pop3 cap15≥游侠12。
        # 阵亡补回：current_population 回落即按该 pop 槽位补(尾段全是游侠,
        # 撤一个补一个游侠);先锋阵亡不强制重建(符合"先锋弱、工人当肉盾")。
        if current_population >= LIGHTNING_MAX_POPULATION:
            return None
        target_type = self._lightning_build_slot(current_population)
        if target_type is None:
            return None
        cost = (
            worker_cost
            if target_type is UnitType.WORKER
            else vanguard_cost
            if target_type is UnitType.VANGUARD
            else ranger_cost
        )
        if budget >= cost:
            return target_type
        return None

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
        near_threat = any(_distance(core.position, enemy.position) <= 5 for enemy in turn.visible_enemies)
        auto_mobility_ready = self._core_auto_mobility_ready(turn)

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
        spawn = (
            self._select_spawn(turn, projected_resources)
            if can_spawn
            else None
        )

        if spawn is not None:
            core.spawn(spawn)
            replacement = self.memory.replacement_queue[spawn.value] > 0
            decisions.append(
                f"core spawn {spawn.value} resources={turn.resources} "
                f"projected={projected_resources} replacement={replacement}"
            )
            self.memory.decision_totals[f"core:spawn:{spawn.value}"] += 1
        elif projected_resources >= 1 and core.shield < shield_cap:
            core.repair_shield()
            decisions.append(f"core repair_shield reason=spare_resources shield={core.shield}")
            self.memory.decision_totals["core:repair"] += 1
        else:
            # 闪电模式：Core 在方环内绕半径 pr 的周界转圈巡逻。安全方环里
            # 不需要战斗护卫才动——巡逻本身帮工人找资源、帮猎手发现 Core。
            # _choose_core_migration 内部已有 8 格内有敌中止 + hp/盾低中止，
            # 上游 _choose_core 已先处理治疗/修盾/产兵，故不设 auto_mobility 门槛。
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
    ) -> None:
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
        if service_workers:
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
            score = (
                target_distance
                + planner.threat.get(destination, 0) * 20
                + heading_penalty
                + combat_proximity_penalty
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
