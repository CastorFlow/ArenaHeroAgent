from __future__ import annotations

import heapq
import json
from collections import Counter
from dataclasses import dataclass, field
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
)


Position = tuple[int, int]
Chunk = tuple[int, int]
CHUNK_SIZE = 32
ROUTES_FILENAME = ".arena_hero_routes.json"
RECOVERY_TARGETS_FILENAME = ".arena_hero_recovery_targets.json"
CONTROL_FILENAME = ".arena_hero_control.json"
STATS_FILENAME = ".arena_hero_stats.json"
ROUTE_OVERLAY_VERSION = 2

MODE_DEVELOP = "develop"
MODE_AGGRESS = "aggress"
MODE_VALUES = {MODE_DEVELOP, MODE_AGGRESS}
DEVELOP_TARGET_WORKERS = 12
DEVELOP_TARGET_VANGUARDS = 2
DEVELOP_TARGET_RANGERS = 2
DEVELOP_SEARCH_INITIAL_RADIUS = 10
DEVELOP_SEARCH_STEP = 12
# 侵略模式：战斗单位优先，工人仅保底经济
AGGRESS_BASE_WORKERS = 4
AGGRESS_TARGET_VANGUARDS = 3
AGGRESS_TARGET_RANGERS = 6
# 人口上限（游戏规则 19）
MAX_POPULATION = 19
# core 是否允许自动迁移（false = 固定不动）
CORE_MIGRATION_ENABLED = False
# 发育探索半径封顶：资源 4 tick 刷新，守点循环采集优于长途探索
DEVELOP_WIDE_SEARCH_MAX_RADIUS = 24
# 卡住判定：单位连续这么多 tick 位置未变化且仍有移动目标 → 视为迷路
STUCK_TICKS = 16
# 打转判定：最近 STUCK_TICKS 个 tick 内，单位经过的不同位置 ≤ 此阈值 → 震荡打转
SPIN_POSITION_BUDGET = 6
# 单位满血值
MAX_HP = {UnitType.WORKER: 2, UnitType.VANGUARD: 4, UnitType.RANGER: 2}
AGGRESS_DEFENDER_VANGUARDS = 1
AGGRESS_DEFENDER_RANGERS = 1
ASSAULT_SIGHTING_MAX_AGE = 20
ASSAULT_FRONTIER_RADII = (14, 22, 30)
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
BEACON_PROGRESS_WEIGHT = 3.0
RANGER_DEFENSE_LEASH_RADIUS = 8
CORE_PATROL_RANGER_COUNT = 2
CORE_PATROL_RADIUS = 2
CORE_PATROL_ROTATION_TICKS = 8
DEFENSE_REPLACEMENT_RESERVE = 10
FRONTIER_BEACON_BACKTRACK_TOLERANCE = 2
REFILL_PROBE_MAX_DISTANCE = 40
REFILL_PROBE_BACKTRACK_DISTANCE = 12
REFILL_PROBE_CORE_LEASH_DISTANCE = 24
LAST_SEEN_RESOURCE_MAX_DISTANCE = 24
LAST_SEEN_RESOURCE_BACKTRACK_DISTANCE = 10


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
    last_tick: int = 0
    mode: str = MODE_DEVELOP
    recall: bool = False
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
    observations: list[str] = field(default_factory=list, repr=False)
    unit_positions: dict[str, Position] = field(default_factory=dict, repr=False)
    last_position_tick: dict[str, int] = field(default_factory=dict, repr=False)
    recent_positions: dict[str, list[Position]] = field(default_factory=dict, repr=False)
    enemy_positions: dict[str, Position] = field(default_factory=dict, repr=False)
    enemy_prev: dict[str, Position] = field(default_factory=dict, repr=False)

    @classmethod
    def load(cls, path: Path) -> TacticMemory:
        if not path.is_file():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
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
            for position in _load_recovery_target_hints(
                path.with_name(RECOVERY_TARGETS_FILENAME)
            ):
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
            memory.last_tick = int(data.get("last_tick", 0))
            memory.mode = data.get("mode", MODE_DEVELOP)
            if memory.mode not in MODE_VALUES:
                memory.mode = MODE_DEVELOP
            memory.recall = bool(data.get("recall", False))
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
            "last_tick": self.last_tick,
            "mode": self.mode,
            "recall": self.recall,
            "total_resources_harvested": self.total_resources_harvested,
            "total_resources_deposited": self.total_resources_deposited,
            "total_resources_captured": self.total_resources_captured,
            "enemy_cores_destroyed": self.enemy_cores_destroyed,
            "first_observed_tick": self.first_observed_tick,
            "observed_turns": self.observed_turns,
            "units_lost": self.units_lost,
        }
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        temporary.replace(path)
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
        previous_unit_ids = set(self.unit_labels)
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
        self.units_lost += len(previous_unit_ids - live_unit_ids)
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
            actor_key = str(event.actor_id) if event.actor_id is not None else None
            if event.event_type == "UNIT_MOVE_FAILED" and actor_key is not None:
                planned = self.planned_moves.pop(actor_key, None)
                if planned is not None and planned.tick == event.tick:
                    if event.reason_code == "MOVE_BLOCKED_TERRAIN":
                        self.known_obstacles.add(planned.destination)
                    else:
                        penalty = 4 if event.reason_code in {
                            "MOVE_CONTESTED",
                            "MOVE_DESTINATION_OCCUPIED",
                            "MOVE_SWAP_BLOCKED",
                        } else 2
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
                self.enemy_cores_destroyed += 1

        self.known_obstacles.update(turn.obstacle_cells)
        visible_enemy_ids = {str(enemy.id) for enemy in turn.visible_enemies}
        for enemy in turn.visible_enemies:
            self.enemy_sightings[str(enemy.id)] = EnemySighting(
                position=enemy.position,
                seen_tick=turn.tick,
                is_core=isinstance(enemy, CoreView),
            )
        self.enemy_sightings = {
            object_id: sighting
            for object_id, sighting in self.enemy_sightings.items()
            if turn.tick - sighting.seen_tick <= ASSAULT_SIGHTING_MAX_AGE
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
                if goal.position not in turn.resource_cells or worker.cargo:
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
        # 追踪单位位置（用于卡住检测：位置变化时刷新 tick）
        for unit in turn.units:
            uid = str(unit.id)
            previous = self.unit_positions.get(uid)
            self.unit_positions[uid] = unit.position
            if previous != unit.position:
                self.last_position_tick[uid] = turn.tick
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
        for eid in list(self.enemy_positions):
            if eid not in {str(e.id) for e in turn.visible_enemies}:
                self.enemy_positions.pop(eid, None)
                self.enemy_prev.pop(eid, None)
        # 清理已不存在的单位卡住追踪
        live_ids = {str(u.id) for u in turn.units}
        for uid in list(self.last_position_tick):
            if uid not in live_ids:
                self.last_position_tick.pop(uid, None)
                self.unit_positions.pop(uid, None)
                self.recent_positions.pop(uid, None)
        self.last_tick = turn.tick

    def remember_move(self, unit: Unit, destination: Position, tick: int) -> None:
        self.planned_moves[str(unit.id)] = PlannedMove(destination=destination, tick=tick)

    def set_worker_goal(self, worker: Worker, kind: str, position: Position, tick: int) -> None:
        self.worker_goals[str(worker.id)] = WorkerGoal(kind, position, tick)

    def clear_worker_goal(self, worker: Worker) -> None:
        self.worker_goals.pop(str(worker.id), None)

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
            mode = data.get("mode", self.mode)
            if mode in MODE_VALUES:
                self.mode = mode
            self.recall = bool(data.get("recall", self.recall))
            self.control_mtime = mtime
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            pass

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
                "mode": self.mode,
                "recall": self.recall,
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
                "owns_beacon": _owns_beacon(turn),
                "visible_resource_cells": len(turn.resource_cells),
                "known_resource_cells": len(self.resource_last_seen),
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
                    if goal.kind == "develop_frontier"
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


def _load_recovery_target_hints(path: Path) -> tuple[Position, ...]:
    if not path.is_file():
        return ()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") != 1:
            return ()
        targets: list[Position] = []
        for value in data.get("targets", ()):
            if not isinstance(value, list) or len(value) != 2:
                continue
            position = int(value[0]), int(value[1])
            if position not in targets:
                targets.append(position)
        return tuple(targets)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return ()


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
    max_expansions: int = 900,
) -> tuple[Direction, ...]:
    if start == goal:
        return ()

    search_radius = max(16, min(160, _distance(start, goal) + 20))
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


class SmartTactic:
    def __init__(
        self,
        memory: TacticMemory | None = None,
        *,
        control_path: Path | None = None,
    ) -> None:
        self.memory = memory or TacticMemory()
        self.control_path = control_path or Path(CONTROL_FILENAME)

    def choose_actions(self, turn: Turn) -> DecisionSummary:
        self.memory.load_control(self.control_path)
        self.memory.observe(turn)
        previous_events = Counter(event.event_type for event in turn.events)
        decisions = list(self.memory.observations)

        if turn.core is None:
            return self._summary(turn, previous_events, decisions)

        planner = MovementPlanner(turn, self.memory, decisions)
        acted_units: set[UUID] = set()
        core_acted = self._choose_beacon(turn, planner, acted_units, decisions)
        self._vacate_core_for_logistics(
            turn,
            planner,
            acted_units,
            decisions,
        )
        incoming_deposit = self._choose_workers(turn, planner, acted_units, decisions)
        self._choose_healing(turn, planner, acted_units, decisions)
        self._choose_vanguards(turn, planner, acted_units, decisions)
        self._choose_rangers(turn, planner, acted_units, decisions)
        self._choose_core(turn, planner, core_acted, incoming_deposit, decisions)
        return self._summary(turn, previous_events, decisions)

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
        needs_core_space = any(worker.cargo for worker in turn.workers) or turn.resources >= 5
        if not needs_core_space:
            return

        near_cargo = any(
            worker.cargo and _distance(worker.position, core.position) <= 3
            for worker in turn.workers
        )
        core_neighborhood = {core.position} | {
            _destination(core.position, direction) for direction in DIRECTION_ORDER
        }
        blockers = [
            unit
            for unit in turn.units
            if (
                unit.position == core.position
                or (near_cargo and unit.position in core_neighborhood)
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
                        for worker in turn.workers
                        if worker.cargo
                    ),
                    default=0,
                ),
                unit.id.bytes,
            )
        )
        for blocker in blockers:
            strategic_goal = turn.beacon.position
            if strategic_goal == core.position:
                direction = self.memory.core_heading or Direction.UP
                dx, dy = direction.delta
                strategic_goal = (core.position[0] + dx * 3, core.position[1] + dy * 3)
            if planner.toward(
                blocker,
                strategic_goal,
                "vacate_core_for_logistics",
            ):
                acted_units.add(blocker.id)
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
            if turn.core is None or turn.beacon.carrier_id == turn.core.id:
                return False
            carrier = next(
                (
                    unit
                    for unit in turn.units
                    if unit.id == turn.beacon.carrier_id
                ),
                None,
            )
            if carrier is None:
                return False
            if (
                turn.core.view.state is CoreState.NORMAL
                and carrier.position == turn.core.position
            ):
                carrier.drop_beacon()
                turn.core.wait()
                acted_units.add(carrier.id)
                decisions.append(
                    f"{carrier.unit_type.value.lower()}:{_short_id(carrier.id)} "
                    "drop_beacon reason=transfer_to_core"
                )
                decisions.append("core wait reason=receive_beacon_next_tick")
                self.memory.decision_totals["unit:drop_beacon_transfer"] += 1
                return True
            rendezvous = (
                turn.core.view.destination
                if turn.core.view.state is CoreState.MOVING
                and turn.core.view.destination is not None
                else turn.core.position
            )
            if carrier.position == rendezvous:
                carrier.wait()
                acted_units.add(carrier.id)
                decisions.append(
                    f"{carrier.unit_type.value.lower()}:{_short_id(carrier.id)} "
                    f"wait reason=beacon_carrier_rendezvous goal={rendezvous}"
                )
                self.memory.decision_totals["unit:wait_beacon_transfer"] += 1
                return False
            if planner.toward(
                carrier,
                rendezvous,
                "beacon_carrier_return",
            ):
                acted_units.add(carrier.id)
            else:
                carrier.wait()
                acted_units.add(carrier.id)
                decisions.append(
                    f"{carrier.unit_type.value.lower()}:{_short_id(carrier.id)} "
                    f"wait reason=beacon_carrier_blocked goal={rendezvous}"
                )
                self.memory.decision_totals["unit:wait_beacon_transfer"] += 1
            if turn.core.view.state is CoreState.NORMAL:
                turn.core.wait()
                decisions.append("core wait reason=beacon_transfer_rendezvous")
                return True
            return False

        if turn.beacon.status is BeaconStatus.GROUND:
            if (
                turn.core is not None
                and turn.core.position == turn.beacon.position
                and turn.core.view.state is CoreState.NORMAL
            ):
                turn.core.pickup_beacon()
                decisions.append("core pickup_beacon reason=standing_on_beacon")
                self.memory.decision_totals["core:pickup_beacon"] += 1
                return True
            candidates = [unit for unit in turn.units if unit.position == turn.beacon.position]
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

        candidates: list[Unit] = list(turn.vanguards)
        if len(turn.rangers) > 1:
            candidates.extend(turn.rangers)
        develop_needs_resource_search = (
            self.memory.mode == MODE_DEVELOP
            and not turn.resource_cells
            and not self.memory.resource_last_seen
            and not self.memory.recovery_targets
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
        return_position = (
            turn.core.view.destination
            if turn.core.view.state is CoreState.MOVING
            and turn.core.view.destination is not None
            else turn.core.position
        )

        for worker in sorted(turn.workers, key=_uuid_key):
            if worker.id in acted_units:
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
                    self.memory.clear_worker_goal(worker)
                    continue
            empty_workers.append(worker)

        unassigned = {worker.id: worker for worker in empty_workers}
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
            )
            if stationary or spinning:
                reason = "stationary" if stationary else "spinning"
                self.memory.clear_worker_goal(worker)
                decisions.append(
                    f"worker:{_short_id(worker.id)} stuck_clear reason={reason} "
                    f"goal={goal.position} unique_cells={len(set(recent))}"
                )
                self.memory.decision_totals["worker:stuck_clear"] += 1
                stuck_cleared += 1
        if stuck_cleared:
            decisions.append(f"worker_stuck_cleared count={stuck_cleared}")
        harvested_cells: set[Position] = set()
        for position in sorted(turn.resource_cells):
            contenders = sorted(
                (worker for worker in empty_workers if worker.position == position),
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
        reserved_targets: set[Position] = set()

        full_capacity = turn.resources >= turn.resource_capacity
        develop_wide_search = (
            self.memory.mode == MODE_DEVELOP
            and not full_capacity
            and not turn.resource_cells
            and not self.memory.resource_last_seen
            and not self.memory.recovery_targets
        )
        if develop_wide_search:
            for worker in unassigned.values():
                goal = self.memory.worker_goals.get(str(worker.id))
                if goal is not None and goal.kind not in {
                    "develop_frontier",
                    "refilled_chunk",
                }:
                    self.memory.clear_worker_goal(worker)

        # Keep a still-visible resource assignment stable instead of switching
        # to whichever point happens to be one step closer on this Tick.
        for worker_id, worker in list(unassigned.items()):
            goal = self.memory.worker_goals.get(str(worker.id))
            if (
                goal is None
                or goal.position not in available_resources
                or goal.position in reserved_targets
            ):
                continue
            self.memory.set_worker_goal(worker, "visible_resource", goal.position, goal.created_tick)
            if planner.toward(
                worker,
                goal.position,
                "visible_resource:continue",
                avoid=(turn.core.position,),
            ):
                reserved_targets.add(goal.position)
                available_resources.discard(goal.position)
                unassigned.pop(worker_id, None)

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
            planner.toward(
                worker,
                goal.position,
                "visible_resource:fog_continue",
                avoid=(turn.core.position,),
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

        self._assign_recovery_target(
            turn,
            planner,
            unassigned,
            reserved_targets,
            decisions,
        )

        for worker_id, worker in list(unassigned.items()):
            goal = self.memory.worker_goals.get(str(worker.id))
            if goal is None or goal.position in reserved_targets:
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
                goal.kind in {"frontier", "develop_frontier"}
                and turn.tick - goal.created_tick > 24
            ):
                self.memory.clear_worker_goal(worker)
                continue
            reserved_targets.add(goal.position)
            if goal.kind == "develop_frontier":
                self.memory.worker_search_radius[str(worker.id)] = max(
                    self.memory.worker_search_radius.get(str(worker.id), 0),
                    _distance(turn.core.position, goal.position),
                )
            if planner.toward(
                worker,
                goal.position,
                goal.kind,
                avoid=(turn.core.position,),
            ):
                unassigned.pop(worker_id, None)
                if goal.kind == "develop_frontier":
                    self.memory.decision_totals["worker:develop_explore"] += 1

        remembered_resources = {
            position
            for position, seen_tick in self.memory.resource_last_seen.items()
            if position not in turn.resource_cells
            and position not in reserved_targets
            and turn.tick - seen_tick <= 12
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

        if not full_capacity:
            self._assign_refilled_chunks(
                turn,
                planner,
                unassigned,
                reserved_targets,
            )

        for worker_id, worker in list(unassigned.items()):
            if full_capacity:
                # 满仓：不派新探索目标，工人就地驻守等待 core 腾空间
                continue
            target = self._frontier_target(
                turn,
                worker,
                reserved_targets,
                planner,
                wide_search=develop_wide_search,
            )
            if target is None:
                continue
            goal_kind = "develop_frontier" if develop_wide_search else "frontier"
            self.memory.set_worker_goal(worker, goal_kind, target, turn.tick)
            if develop_wide_search:
                self.memory.worker_search_radius[str(worker.id)] = max(
                    self.memory.worker_search_radius.get(str(worker.id), 0),
                    _distance(turn.core.position, target),
                )
            reserved_targets.add(target)
            if planner.toward(
                worker,
                target,
                goal_kind,
                avoid=(turn.core.position,),
            ):
                unassigned.pop(worker_id, None)
                if develop_wide_search:
                    self.memory.decision_totals["worker:develop_explore"] += 1
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
            if planner.toward(
                worker,
                target,
                kind,
                avoid=(turn.core.position,) if turn.core is not None else (),
            ):
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
            if planner.toward(
                worker,
                target,
                "resource_recovery:continue",
                avoid=(turn.core.position,),
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
        if turn.resource_cells or available_slots <= 0 or not unassigned:
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
            if planner.toward(
                worker,
                target,
                "resource_recovery",
                avoid=(turn.core.position,),
            ):
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
        return max(1, len(turn.workers) // 3)

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
        for worker_id, worker in unassigned.items():
            goal = self.memory.worker_goals.get(str(worker_id))
            if goal is None or goal.kind != "refilled_chunk":
                continue
            outside_core_leash = (
                owns_beacon
                and turn.core is not None
                and _distance(goal.position, turn.core.position)
                > REFILL_PROBE_CORE_LEASH_DISTANCE
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
        strategic_core = turn.core.position if owns_beacon else None
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
                and turn.tick - self.memory.chunk_last_probe.get(chunk, -1000) >= 8
                and chunk not in active_chunks
                and _distance(
                    turn.core.position,
                    self.memory.chunk_anchors.get(
                        chunk,
                        (chunk[0] * CHUNK_SIZE + 16, chunk[1] * CHUNK_SIZE + 16),
                    ),
                )
                <= REFILL_PROBE_CORE_LEASH_DISTANCE
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
            )
            if target is None or target in reserved_targets:
                continue
            self.memory.set_worker_goal(worker, "refilled_chunk", target, turn.tick)
            if planner.toward(
                worker,
                target,
                "refilled_chunk",
                avoid=(turn.core.position,) if turn.core is not None else (),
            ):
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
                    or _distance(position, strategic_core)
                    <= REFILL_PROBE_CORE_LEASH_DISTANCE
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
        for unit in sorted(turn.units, key=_uuid_key):
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
                continue
            if planner.toward(unit, core.position, "heal_return"):
                acted_units.add(unit.id)
                decisions.append(
                    f"{unit.unit_type.value.lower()}:{_short_id(unit.id)} heal_return "
                    f"hp={unit.hp}/{max_hp}"
                )
                self.memory.decision_totals["unit:heal_return"] += 1

    def _choose_vanguards(
        self,
        turn: Turn,
        planner: MovementPlanner,
        acted_units: set[UUID],
        decisions: list[str],
    ) -> None:
        if self.memory.recall:
            self._choose_vanguards_recall(turn, planner, acted_units, decisions)
        elif self.memory.mode == MODE_AGGRESS:
            self._choose_vanguards_aggress(turn, planner, acted_units, decisions)
        else:
            self._choose_vanguards_defend(turn, planner, acted_units, decisions)

    def _sweep_targets(self, vanguard: Vanguard, turn: Turn) -> Direction | None:
        sweep_options: list[tuple[int, int, Direction]] = []
        for direction in DIRECTION_ORDER:
            target_cell = _destination(vanguard.position, direction)
            targets = [
                enemy
                for enemy in turn.visible_enemies
                if enemy.position == target_cell
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

    def _pick_assault_target(self, turn: Turn) -> Position | None:
        origin = turn.core.position if turn.core is not None else (0, 0)
        if turn.visible_enemies:
            cores = [
                enemy for enemy in turn.visible_enemies if isinstance(enemy, CoreView)
            ]
            if cores:
                nearest = min(
                    cores,
                    key=lambda enemy: (_distance(origin, enemy.position), enemy.id.bytes),
                )
                return nearest.position
            nearest = min(
                turn.visible_enemies,
                key=lambda enemy: (
                    _enemy_role_priority(enemy),
                    _distance(origin, enemy.position),
                    enemy.id.bytes,
                ),
            )
            return nearest.position

        if not self.memory.enemy_sightings:
            return None
        sighting = min(
            self.memory.enemy_sightings.values(),
            key=lambda candidate: (
                0 if candidate.is_core else 1,
                turn.tick - candidate.seen_tick,
                _distance(origin, candidate.position),
                candidate.position,
            ),
        )
        return sighting.position

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

        sector = (turn.tick // 64) % 4
        preferred_signs = ((1, 1), (-1, 1), (-1, -1), (1, -1))[sector]
        candidates: set[Position] = set()
        for radius in ASSAULT_FRONTIER_RADII:
            for dx in range(-radius, radius + 1):
                dy = radius - abs(dx)
                candidates.add((origin[0] + dx, origin[1] + dy))
                candidates.add((origin[0] + dx, origin[1] - dy))
        candidates.difference_update(planner.obstacles)
        if not candidates:
            return None

        owns_beacon = _owns_beacon(turn)
        core_beacon_distance = _distance(origin, turn.beacon.position)

        def score(position: Position) -> tuple[float, Position]:
            dx = position[0] - origin[0]
            dy = position[1] - origin[1]
            sector_penalty = (
                0
                if dx * preferred_signs[0] >= 0 and dy * preferred_signs[1] >= 0
                else 18
            )
            beacon_progress = 0
            if not owns_beacon:
                beacon_progress = core_beacon_distance - _distance(
                    position, turn.beacon.position
                )
            return (
                self.memory.visited.get(position, 0) * 30
                + planner.threat.get(position, 0) * 25
                + sector_penalty
                + _distance(origin, position) * 0.03
                - _chunk_quota(_chunk_of(position)) * 0.2
                - beacon_progress * 1.5,
                position,
            )

        return min(candidates, key=score)

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

    def _ranger_shot_candidates(
        self,
        turn: Turn,
        ranger: Ranger,
        planner: MovementPlanner,
    ) -> list[tuple[UnitView | CoreView, Position]]:
        """返回 (敌人, 射击格) 候选：预判格优先，当前位置兜底。"""
        candidates: list[tuple[UnitView | CoreView, Position]] = []
        for enemy in turn.visible_enemies:
            predicted = self._predicted_enemy_cell(turn, enemy)
            if _is_legal_ranger_shot(ranger.position, predicted, planner.obstacles):
                candidates.append((enemy, predicted))
            elif _is_legal_ranger_shot(
                ranger.position,
                enemy.position,
                planner.obstacles,
            ):
                candidates.append((enemy, enemy.position))
        return candidates

    def _choose_vanguards_aggress(
        self,
        turn: Turn,
        planner: MovementPlanner,
        acted_units: set[UUID],
        decisions: list[str],
    ) -> None:
        ordered = sorted(turn.vanguards, key=_uuid_key)
        defender_count = min(
            AGGRESS_DEFENDER_VANGUARDS,
            max(0, len(ordered) - 1),
        )
        defender_ids = {unit.id for unit in ordered[:defender_count]}
        combat_target = self._pick_assault_target(turn)
        frontier_target = self._assault_frontier_target(turn, planner)
        for vanguard in ordered:
            if vanguard.id in acted_units:
                continue
            direction = self._sweep_targets(vanguard, turn)
            if direction is not None:
                vanguard.sweep(direction)
                decisions.append(
                    f"vanguard:{_short_id(vanguard.id)} sweep {direction.value} reason=aggress"
                )
                self.memory.decision_totals["vanguard:sweep"] += 1
                continue
            if turn.core is not None:
                # 家被摸：先救家再出击
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
                    planner.toward(vanguard, target.position, "aggress_defend_core")
                    continue
            if vanguard.id in defender_ids:
                if (
                    turn.core is not None
                    and _distance(vanguard.position, turn.core.position) > 2
                ):
                    planner.toward(vanguard, turn.core.position, "aggress_core_guard")
                self.memory.decision_totals["vanguard:aggress_guard"] += 1
                continue
            if combat_target is not None:
                planner.toward(vanguard, combat_target, "assault_enemy")
                decisions.append(
                    f"vanguard:{_short_id(vanguard.id)} assault target={combat_target}"
                )
                self.memory.decision_totals["vanguard:assault"] += 1
                continue
            if frontier_target is not None:
                planner.toward(vanguard, frontier_target, "aggress_frontier")
                self.memory.decision_totals["vanguard:frontier"] += 1

    def _choose_vanguards_recall(
        self,
        turn: Turn,
        planner: MovementPlanner,
        acted_units: set[UUID],
        decisions: list[str],
    ) -> None:
        for vanguard in sorted(turn.vanguards, key=_uuid_key):
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
                if _distance(vanguard.position, turn.core.position) > 1:
                    planner.toward(vanguard, turn.core.position, "recall_guard_core")
                    self.memory.decision_totals["vanguard:recall"] += 1

    def _choose_vanguards_defend(
        self,
        turn: Turn,
        planner: MovementPlanner,
        acted_units: set[UUID],
        decisions: list[str],
    ) -> None:
        for vanguard in sorted(turn.vanguards, key=_uuid_key):
            if vanguard.id in acted_units:
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
        elif self.memory.mode == MODE_AGGRESS:
            self._choose_rangers_aggress(turn, planner, acted_units, decisions)
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
        defender_count = min(
            AGGRESS_DEFENDER_RANGERS,
            max(0, len(ordered) - 1),
        )
        defenders = ordered[:defender_count]
        defender_ids = {unit.id for unit in defenders}
        patrol_slots = self._core_patrol_slots(turn, planner, defenders)
        combat_target = self._pick_assault_target(turn)
        frontier_target = self._assault_frontier_target(turn, planner)
        for ranger in ordered:
            if ranger.id in acted_units:
                continue
            shot_candidates = self._ranger_shot_candidates(turn, ranger, planner)
            if ranger.id in defender_ids:
                shot_candidates = [
                    (enemy, cell)
                    for enemy, cell in shot_candidates
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
                assigned_damage[target.id] += 1
                decisions.append(
                    f"ranger:{_short_id(ranger.id)} shoot target={_short_id(target.id)} "
                    f"expected={cell} role=aggress"
                )
                self.memory.decision_totals["ranger:shoot"] += 1
                continue
            # 移动：向敌人（Core 优先）推进到射程内
            if ranger.id in defender_ids:
                patrol_slot = patrol_slots.get(ranger.id)
                if patrol_slot is not None and ranger.position != patrol_slot:
                    planner.toward(ranger, patrol_slot, "aggress_core_patrol")
                elif (
                    turn.core is not None
                    and _distance(ranger.position, turn.core.position) > 2
                ):
                    planner.toward(ranger, turn.core.position, "aggress_core_guard")
                self.memory.decision_totals["ranger:aggress_guard"] += 1
                continue
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
                planner.toward(ranger, frontier_target, "aggress_frontier")
                self.memory.decision_totals["ranger:frontier"] += 1

    def _choose_rangers_recall(
        self,
        turn: Turn,
        planner: MovementPlanner,
        acted_units: set[UUID],
        decisions: list[str],
    ) -> None:
        assigned_damage: Counter[UUID] = Counter()
        ordered_rangers = sorted(
            turn.rangers,
            key=lambda ranger: (
                self.memory.unit_labels.get(
                    str(ranger.id),
                    UnitLabel(UnitType.RANGER.value, 1_000_000),
                ).number,
                ranger.id.bytes,
            ),
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
                planner.toward(ranger, turn.core.position, "ranger_recall_core")
                self.memory.decision_totals["ranger:recall"] += 1

    def _choose_rangers_defend(
        self,
        turn: Turn,
        planner: MovementPlanner,
        acted_units: set[UUID],
        decisions: list[str],
    ) -> None:
        assigned_damage: Counter[UUID] = Counter()
        idle: list[Ranger] = []
        ordered_rangers = sorted(
            turn.rangers,
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
            turn.rangers,
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

    def _core_patrol_slots(
        self,
        turn: Turn,
        planner: MovementPlanner,
        patrol_rangers: list[Ranger],
    ) -> dict[UUID, Position]:
        if turn.core is None or not patrol_rangers:
            return {}
        offsets = (
            (0, -CORE_PATROL_RADIUS),
            (CORE_PATROL_RADIUS, 0),
            (0, CORE_PATROL_RADIUS),
            (-CORE_PATROL_RADIUS, 0),
        )
        phase = (turn.tick // CORE_PATROL_ROTATION_TICKS) % len(offsets)
        reserved: set[Position] = set()
        slots: dict[UUID, Position] = {}
        for index, ranger in enumerate(patrol_rangers):
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

        workers = len(turn.workers)
        rangers = len(turn.rangers)
        vanguards = len(turn.vanguards)
        reserve = 2 if near_threat or core.shield < shield_cap else 0
        budget = projected_resources - reserve
        spawn: UnitType | None = None
        can_spawn = (
            planner.final_occupancy(core.position) < 2
            and len(turn.units) < MAX_POPULATION
        )

        mode = self.memory.mode
        recall = self.memory.recall

        if can_spawn:
            if recall:
                # 召回模式：全力补防御
                if vanguards < 2 and budget >= 10:
                    spawn = UnitType.VANGUARD
                elif rangers < 3 and budget >= 12:
                    spawn = UnitType.RANGER
                elif workers < AGGRESS_BASE_WORKERS and budget >= 5:
                    spawn = UnitType.WORKER
            elif mode == MODE_DEVELOP:
                # 发育模式：12 名工人扩张经济，仅留 2+2 的核心守军。
                if workers < 4 and budget >= 5:
                    spawn = UnitType.WORKER
                elif vanguards < 1 and budget >= 10:
                    spawn = UnitType.VANGUARD
                elif rangers < 1 and budget >= 12:
                    spawn = UnitType.RANGER
                elif (
                    near_threat
                    and rangers < 4
                    and budget >= 12
                    and projected_resources - 12 >= DEFENSE_REPLACEMENT_RESERVE
                ):
                    spawn = UnitType.RANGER
                elif (
                    near_threat
                    and vanguards < 4
                    and budget >= 10
                    and projected_resources - 10 >= DEFENSE_REPLACEMENT_RESERVE
                ):
                    spawn = UnitType.VANGUARD
                elif near_threat:
                    pass
                elif (
                    workers < DEVELOP_TARGET_WORKERS
                    and budget >= 5
                    and projected_resources - 5 >= DEFENSE_REPLACEMENT_RESERVE
                ):
                    spawn = UnitType.WORKER
                elif rangers < DEVELOP_TARGET_RANGERS and budget >= 12:
                    spawn = UnitType.RANGER
                elif vanguards < DEVELOP_TARGET_VANGUARDS and budget >= 10:
                    spawn = UnitType.VANGUARD
            elif mode == MODE_AGGRESS:
                # 侵略模式：战斗单位优先，工人仅保底经济
                if workers < AGGRESS_BASE_WORKERS and budget >= 5:
                    spawn = UnitType.WORKER
                elif near_threat and vanguards < 1 and budget >= 10:
                    spawn = UnitType.VANGUARD
                elif rangers < AGGRESS_TARGET_RANGERS and budget >= 12:
                    spawn = UnitType.RANGER
                elif vanguards < AGGRESS_TARGET_VANGUARDS and budget >= 10:
                    spawn = UnitType.VANGUARD
                elif rangers < 8 and budget >= 12:
                    spawn = UnitType.RANGER
                elif vanguards < 5 and budget >= 10:
                    spawn = UnitType.VANGUARD
                elif (
                    workers < 6
                    and not near_threat
                    and budget >= 5
                    and projected_resources - 5 >= DEFENSE_REPLACEMENT_RESERVE
                ):
                    spawn = UnitType.WORKER

        if spawn is not None:
            core.spawn(spawn)
            decisions.append(
                f"core spawn {spawn.value} resources={turn.resources} projected={projected_resources}"
            )
            self.memory.decision_totals[f"core:spawn:{spawn.value}"] += 1
        elif projected_resources >= 1 and core.shield < shield_cap:
            core.repair_shield()
            decisions.append(f"core repair_shield reason=spare_resources shield={core.shield}")
            self.memory.decision_totals["core:repair"] += 1
        else:
            if CORE_MIGRATION_ENABLED:
                self._choose_core_migration(
                    turn,
                    planner,
                    incoming_deposit,
                    decisions,
                )

    def _choose_core_migration(
        self,
        turn: Turn,
        planner: MovementPlanner,
        incoming_deposit: int,
        decisions: list[str],
    ) -> None:
        core = turn.core
        if core is None or core.view.state is not CoreState.NORMAL:
            return
        cargo_workers = [worker for worker in turn.workers if worker.cargo]
        if incoming_deposit > 0 or any(
            _distance(core.position, worker.position) <= 5
            for worker in cargo_workers
        ):
            return
        if core.hp < 5 or core.shield < 3:
            return
        if any(
            _distance(core.position, enemy.position) <= 8
            for enemy in turn.visible_enemies
        ):
            return
        owns_beacon = _owns_beacon(turn)

        if cargo_workers:
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
                or destination in turn.resource_cells
                or self.memory.temporary_blocks.get(destination, 0) > turn.tick
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
                if beacon_progress < 0:
                    continue
            score = (
                target_distance
                + planner.threat.get(destination, 0) * 20
                + heading_penalty
                - _chunk_quota(_chunk_of(destination)) * 0.1
                - min(10, self.memory.visited.get(destination, 0)) * 0.05
                - beacon_progress * BEACON_PROGRESS_WEIGHT
            )
            candidates.append(
                (score, DIRECTION_RANK[direction], direction, destination)
            )
        if not candidates:
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
