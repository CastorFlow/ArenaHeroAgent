from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

from arena_hero import (
    Accepted,
    BeaconStatus,
    ChampionBeacon,
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
    RepairShieldAction,
    ResolutionEvent,
    ShootAction,
    SpawnAction,
    StartMoveAction,
    TerrainView,
    Turn,
    UnitType,
    UnitView,
    WaitAction,
)

from arena_hero_tactic import choose_actions
from arena_hero_strategy import (
    MODE_AGGRESS,
    MODE_DEVELOP,
    PlannedMove,
    ROUTES_FILENAME,
    SmartTactic,
    TacticMemory,
    UnitLabel,
    WorkerGoal,
    _chunk_of,
    _chunk_quota,
    _refill_tick_at_or_after,
)


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


def ranger(position: tuple[int, int], unit_id: UUID = RANGER_ID) -> UnitView:
    return UnitView(
        kind="UNIT",
        id=unit_id,
        controlled=True,
        position=position,
        hp=2,
        unit_type=UnitType.RANGER,
    )


def vanguard(
    position: tuple[int, int],
    unit_id: UUID = VANGUARD_ID,
) -> UnitView:
    return UnitView(
        kind="UNIT",
        id=unit_id,
        controlled=True,
        position=position,
        hp=4,
        unit_type=UnitType.VANGUARD,
    )


def enemy_ranger(position: tuple[int, int], *, hp: int = 2) -> UnitView:
    return UnitView(
        kind="UNIT",
        id=ENEMY_RANGER_ID,
        controlled=False,
        position=position,
        hp=hp,
        unit_type=UnitType.RANGER,
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
        population_tier=population // 20,
        upkeep_next_tick=0,
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


class BalancedTacticTests(unittest.TestCase):
    def test_respawning_submits_an_empty_plan(self) -> None:
        turn, submitted = make_turn(own_core=None)

        summary = choose_actions(turn)
        accepted = turn.submit()

        self.assertTrue(accepted.accepted)
        self.assertEqual(summary.unit_actions, 0)
        self.assertIsNone(submitted[0][0].core_action)
        self.assertEqual(dict(submitted[0][0].unit_actions), {})

    def test_worker_deposits_when_sharing_receptive_core(self) -> None:
        turn, _ = make_turn(
            own_core=core((0, 0)),
            units=(worker(WORKER_LOW, (0, 0), cargo=1),),
        )

        choose_actions(turn)

        self.assertIsInstance(turn.plan.unit_actions[WORKER_LOW], DepositAction)

    def test_worker_keeps_cargo_when_colocated_core_is_full(self) -> None:
        turn, _ = make_turn(
            own_core=core((0, 0)),
            units=(worker(WORKER_LOW, (0, 0), cargo=1),),
            resources=10,
        )

        choose_actions(turn)

        self.assertNotIn(WORKER_LOW, turn.plan.unit_actions)

    def test_lowest_uuid_is_only_harvest_contender_on_shared_cell(self) -> None:
        turn, _ = make_turn(
            own_core=core(),
            units=(worker(WORKER_HIGH, (0, 0)), worker(WORKER_LOW, (0, 0))),
            resource_cells=((0, 0),),
        )

        choose_actions(turn)

        self.assertIsInstance(turn.plan.unit_actions[WORKER_LOW], HarvestAction)
        self.assertNotIsInstance(turn.plan.unit_actions.get(WORKER_HIGH), HarvestAction)

    def test_worker_routes_around_visible_obstacle(self) -> None:
        turn, _ = make_turn(
            own_core=core(),
            units=(worker(WORKER_LOW, (0, 0)),),
            resource_cells=((2, 0),),
            obstacle_cells=((1, 0),),
        )

        choose_actions(turn)

        action = turn.plan.unit_actions[WORKER_LOW]
        self.assertIsInstance(action, MoveAction)
        self.assertEqual(action.direction, Direction.UP)

    def test_exported_route_contains_complete_obstacle_aware_path(self) -> None:
        memory = TacticMemory()
        turn, _ = make_turn(
            own_core=core(),
            units=(worker(WORKER_LOW, (0, 0)),),
            resource_cells=((2, 0),),
            obstacle_cells=((1, 0),),
        )

        SmartTactic(memory).choose_actions(turn)

        route = memory.current_routes[str(WORKER_LOW)]
        self.assertEqual(route.start, (0, 0))
        self.assertEqual(route.goal, (2, 0))
        self.assertEqual(route.path[0], route.start)
        self.assertEqual(route.path[-1], route.goal)
        self.assertNotIn((1, 0), route.path)
        self.assertTrue(route.complete)

    def test_route_export_is_atomic_and_contains_no_credentials(self) -> None:
        memory = TacticMemory()
        turn, _ = make_turn(
            tick=18,
            own_core=core(),
            units=(worker(WORKER_LOW, (0, 0)),),
            resource_cells=((2, 0),),
        )
        SmartTactic(memory).choose_actions(turn)

        with TemporaryDirectory() as directory:
            memory_path = Path(directory) / ".arena_hero_memory.json"
            memory.save(memory_path)
            routes_path = memory_path.with_name(ROUTES_FILENAME)
            payload = json.loads(routes_path.read_text(encoding="utf-8"))

        self.assertEqual(payload["tick"], 18)
        self.assertEqual(payload["version"], 2)
        self.assertEqual(payload["routes"][0]["path"][0], [0, 0])
        self.assertEqual(payload["routes"][0]["number"], 1)
        self.assertEqual(payload["resources"], [[2, 0]])
        self.assertEqual(payload["units"][0]["object_type"], "WORKER")
        self.assertEqual(payload["units"][0]["number"], 1)
        serialized = json.dumps(payload).lower()
        self.assertNotIn("api_key", serialized)
        self.assertNotIn("authorization", serialized)
        self.assertNotIn("bearer", serialized)

    def test_unit_numbers_are_stable_per_type_and_not_reused(self) -> None:
        memory = TacticMemory()
        first, _ = make_turn(
            own_core=core(),
            units=(
                worker(WORKER_HIGH, (1, 0)),
                ranger((2, 0)),
                worker(WORKER_LOW, (0, 0)),
            ),
        )

        SmartTactic(memory).choose_actions(first)

        self.assertEqual(memory.unit_labels[str(WORKER_LOW)], UnitLabel("WORKER", 1))
        self.assertEqual(memory.unit_labels[str(WORKER_HIGH)], UnitLabel("WORKER", 2))
        self.assertEqual(memory.unit_labels[str(RANGER_ID)], UnitLabel("RANGER", 1))

        second, _ = make_turn(
            tick=9,
            own_core=core(),
            units=(
                worker(WORKER_THIRD, (3, 0)),
                worker(WORKER_HIGH, (2, 0)),
                ranger((1, 0)),
            ),
        )
        SmartTactic(memory).choose_actions(second)

        self.assertEqual(memory.unit_labels[str(WORKER_HIGH)].number, 2)
        self.assertEqual(memory.unit_labels[str(WORKER_THIRD)].number, 3)
        self.assertEqual(memory.unit_labels[str(RANGER_ID)].number, 1)

    def test_current_state_retargets_after_resource_depletion(self) -> None:
        event = ResolutionEvent(
            event_id=UUID("00000000-0000-4000-8000-000000000300"),
            tick=7,
            event_type="HARVEST_FAILED",
            reason_code="RESOURCE_DEPLETED",
            actor_id=WORKER_LOW,
            position=(0, 0),
        )
        turn, _ = make_turn(
            own_core=core(),
            units=(worker(WORKER_LOW, (0, 0)),),
            resource_cells=((2, 0),),
            events=(event,),
        )

        choose_actions(turn)

        action = turn.plan.unit_actions[WORKER_LOW]
        self.assertIsInstance(action, MoveAction)
        self.assertEqual(action.direction, Direction.RIGHT)

    def test_current_resource_cell_can_represent_remaining_cargo_pile(self) -> None:
        event = ResolutionEvent(
            event_id=UUID("00000000-0000-4000-8000-000000000301"),
            tick=7,
            event_type="HARVEST_SUCCEEDED",
            actor_id=WORKER_HIGH,
            position=(0, 0),
            values={"amount": 1, "source": "DROPPED_CARGO"},
        )
        turn, _ = make_turn(
            own_core=core(),
            units=(worker(WORKER_LOW, (0, 0)),),
            resource_cells=((0, 0),),
            events=(event,),
        )

        choose_actions(turn)

        self.assertIsInstance(turn.plan.unit_actions[WORKER_LOW], HarvestAction)

    def test_resource_disappearance_does_not_reuse_old_turn_controller(self) -> None:
        first, _ = make_turn(
            tick=7,
            own_core=core(),
            units=(worker(WORKER_LOW, (0, 0)),),
            resource_cells=((0, 0),),
        )
        second, _ = make_turn(
            tick=8,
            own_core=core(),
            units=(worker(WORKER_LOW, (0, 0)),),
        )

        choose_actions(first)
        choose_actions(second)

        self.assertIsInstance(first.plan.unit_actions[WORKER_LOW], HarvestAction)
        self.assertNotIsInstance(second.plan.unit_actions.get(WORKER_LOW), HarvestAction)

    def test_ranger_shoots_visible_cardinal_target(self) -> None:
        turn, _ = make_turn(
            own_core=core(),
            units=(ranger((0, 0)),),
            enemies=(enemy_core((0, 3)),),
        )

        choose_actions(turn)

        action = turn.plan.unit_actions[RANGER_ID]
        self.assertIsInstance(action, ShootAction)
        self.assertEqual(action.target_id, ENEMY_CORE_ID)
        self.assertEqual(action.expected_cell, (0, 3))

    def test_ranger_shoots_visible_diagonal_target(self) -> None:
        turn, _ = make_turn(
            own_core=core(),
            units=(ranger((0, 0)),),
            enemies=(enemy_core((2, 2)),),
        )

        choose_actions(turn)

        action = turn.plan.unit_actions[RANGER_ID]
        self.assertIsInstance(action, ShootAction)
        self.assertEqual(action.target_id, ENEMY_CORE_ID)
        self.assertEqual(action.expected_cell, (2, 2))

    def test_ranger_diagonal_shot_ignores_obstacle_beside_line(self) -> None:
        turn, _ = make_turn(
            own_core=core(),
            units=(ranger((0, 0)),),
            enemies=(enemy_core((2, 2)),),
            obstacle_cells=((1, 0),),
        )

        choose_actions(turn)

        self.assertIsInstance(turn.plan.unit_actions[RANGER_ID], ShootAction)

    def test_ranger_diagonal_shot_is_blocked_by_intermediate_obstacle(self) -> None:
        turn, _ = make_turn(
            own_core=core(),
            units=(ranger((0, 0)),),
            enemies=(enemy_core((2, 2)),),
            obstacle_cells=((1, 1),),
        )

        choose_actions(turn)

        self.assertNotIsInstance(turn.plan.unit_actions.get(RANGER_ID), ShootAction)

    def test_ranger_does_not_shoot_through_obstacle(self) -> None:
        turn, _ = make_turn(
            own_core=core(),
            units=(ranger((0, 0)),),
            enemies=(enemy_core((0, 3)),),
            obstacle_cells=((0, 1),),
        )

        choose_actions(turn)

        self.assertNotIsInstance(turn.plan.unit_actions.get(RANGER_ID), ShootAction)

    def test_ranger_returns_to_core_instead_of_chasing_distant_enemy(self) -> None:
        turn, _ = make_turn(
            own_core=core((0, 0)),
            units=(ranger((8, 0)),),
            enemies=(enemy_ranger((12, 0)),),
        )

        summary = SmartTactic(TacticMemory()).choose_actions(turn)

        action = turn.plan.unit_actions[RANGER_ID]
        self.assertIsInstance(action, MoveAction)
        self.assertEqual(action.direction, Direction.LEFT)
        self.assertTrue(any("reason=ranger_core_patrol" in item for item in summary.decisions))

    def test_ranger_pursues_enemy_inside_core_defense_leash(self) -> None:
        turn, _ = make_turn(
            own_core=core((0, 0)),
            units=(ranger((0, 0)),),
            enemies=(enemy_ranger((8, 0)),),
        )

        SmartTactic(TacticMemory()).choose_actions(turn)

        action = turn.plan.unit_actions[RANGER_ID]
        self.assertIsInstance(action, MoveAction)
        self.assertEqual(action.direction, Direction.RIGHT)

    def test_two_oldest_rangers_receive_opposite_core_patrol_slots(self) -> None:
        memory = TacticMemory()
        turn, _ = make_turn(
            tick=8,
            own_core=core((0, 0)),
            units=(
                ranger((0, -5), RANGER_ID),
                ranger((0, 5), RANGER_TWO_ID),
                ranger((5, 5), RANGER_THREE_ID),
            ),
        )

        SmartTactic(memory).choose_actions(turn)

        patrol_routes = [
            route
            for route in memory.current_routes.values()
            if route.reason.startswith("ranger_core_patrol")
        ]
        self.assertEqual(len(patrol_routes), 2)
        self.assertEqual(
            {route.object_id for route in patrol_routes},
            {str(RANGER_ID), str(RANGER_TWO_ID)},
        )
        self.assertEqual(
            {route.goal for route in patrol_routes},
            {(-2, 0), (2, 0)},
        )

    def test_core_patrol_reports_and_engages_nearby_enemy(self) -> None:
        memory = TacticMemory()
        turn, _ = make_turn(
            own_core=core((0, 0)),
            units=(ranger((0, 0), RANGER_ID), ranger((0, 1), RANGER_TWO_ID)),
            enemies=(enemy_ranger((0, 3)),),
        )

        summary = SmartTactic(memory).choose_actions(turn)

        self.assertIsInstance(turn.plan.unit_actions[RANGER_ID], ShootAction)
        self.assertIsInstance(turn.plan.unit_actions[RANGER_TWO_ID], ShootAction)
        self.assertTrue(any("core_patrol_alert count=1" in item for item in summary.decisions))
        self.assertEqual(
            sum("role=core_patrol" in item for item in summary.decisions),
            2,
        )
        self.assertEqual(memory.decision_totals["core_patrol:alert"], 1)
        self.assertEqual(memory.decision_totals["core_patrol:shoot"], 2)

    def test_owned_worker_beacon_carrier_returns_to_core(self) -> None:
        turn, _ = make_turn(
            own_core=core((0, 0)),
            units=(worker(WORKER_LOW, (3, 0)),),
            beacon=ChampionBeacon(
                position=(3, 0),
                status=BeaconStatus.CARRIED,
                carrier_id=WORKER_LOW,
            ),
        )

        summary = SmartTactic(TacticMemory()).choose_actions(turn)

        action = turn.plan.unit_actions[WORKER_LOW]
        self.assertIsInstance(action, MoveAction)
        self.assertEqual(action.direction, Direction.LEFT)
        self.assertIsInstance(turn.plan.core_action, WaitAction)
        self.assertTrue(
            any("reason=beacon_carrier_return" in item for item in summary.decisions)
        )

    def test_owned_worker_beacon_carrier_transfers_to_normal_core(self) -> None:
        turn, _ = make_turn(
            own_core=core((0, 0)),
            units=(worker(WORKER_LOW, (0, 0)),),
            beacon=ChampionBeacon(
                position=(0, 0),
                status=BeaconStatus.CARRIED,
                carrier_id=WORKER_LOW,
            ),
        )

        summary = SmartTactic(TacticMemory()).choose_actions(turn)

        self.assertIsInstance(turn.plan.unit_actions[WORKER_LOW], DropBeaconAction)
        self.assertIsInstance(turn.plan.core_action, WaitAction)
        self.assertTrue(
            any("reason=transfer_to_core" in item for item in summary.decisions)
        )

    def test_core_spawns_worker_conservatively(self) -> None:
        turn, _ = make_turn(
            own_core=core((0, 0)),
            units=(worker(WORKER_LOW, (1, 0)),),
            resources=5,
        )

        choose_actions(turn)

        self.assertIsInstance(turn.plan.core_action, SpawnAction)
        self.assertEqual(turn.plan.core_action.unit_type, UnitType.WORKER)

    def test_core_repairs_when_enemy_threatens(self) -> None:
        turn, _ = make_turn(
            own_core=core((0, 0), shield=2),
            units=(worker(WORKER_LOW, (1, 0)),),
            enemies=(enemy_core((0, 3)),),
            resources=5,
        )

        choose_actions(turn)

        self.assertIsInstance(turn.plan.core_action, RepairShieldAction)

    def test_core_heals_hp_before_repairing_shield(self) -> None:
        turn, _ = make_turn(
            own_core=core((0, 0), hp=3, shield=2),
            units=(worker(WORKER_LOW, (1, 0)),),
            enemies=(enemy_core((0, 3)),),
            resources=2,
        )

        choose_actions(turn)

        self.assertIsInstance(turn.plan.core_action, HealAction)

    def test_core_keeps_replacement_reserve_when_defense_is_fully_staffed(
        self,
    ) -> None:
        turn, _ = make_turn(
            own_core=core((0, 0)),
            units=(
                worker(WORKER_LOW, (6, 0)),
                worker(WORKER_HIGH, (7, 0)),
                worker(WORKER_THIRD, (8, 0)),
                worker(WORKER_FOURTH, (9, 0)),
                worker(WORKER_FIFTH, (10, 0)),
                worker(WORKER_SIXTH, (11, 0)),
                vanguard((3, 3)),
                vanguard((4, 3), VANGUARD_TWO_ID),
                ranger((3, 4)),
                ranger((4, 4), RANGER_TWO_ID),
            ),
            enemies=(enemy_core((0, 3)),),
            resources=19,
        )

        SmartTactic(TacticMemory()).choose_actions(turn)

        self.assertNotIsInstance(turn.plan.core_action, SpawnAction)

    def test_core_uses_final_population_slot_for_third_vanguard_with_reserve(
        self,
    ) -> None:
        turn, _ = make_turn(
            own_core=core((0, 0)),
            units=(
                worker(WORKER_LOW, (6, 0)),
                worker(WORKER_HIGH, (7, 0)),
                worker(WORKER_THIRD, (8, 0)),
                worker(WORKER_FOURTH, (9, 0)),
                worker(WORKER_FIFTH, (10, 0)),
                worker(WORKER_SIXTH, (11, 0)),
                vanguard((3, 3)),
                vanguard((4, 3), VANGUARD_TWO_ID),
                ranger((3, 4)),
                ranger((4, 4), RANGER_TWO_ID),
                ranger((5, 4), RANGER_THREE_ID),
            ),
            resources=30,
        )

        SmartTactic(TacticMemory()).choose_actions(turn)

        self.assertIsInstance(turn.plan.core_action, SpawnAction)
        self.assertEqual(turn.plan.core_action.unit_type, UnitType.VANGUARD)

    def test_core_expands_workers_after_defense_is_fully_staffed(self) -> None:
        turn, _ = make_turn(
            own_core=core((0, 0)),
            units=(
                worker(WORKER_LOW, (6, 0)),
                worker(WORKER_HIGH, (7, 0)),
                worker(WORKER_THIRD, (8, 0)),
                worker(WORKER_FOURTH, (9, 0)),
                worker(WORKER_FIFTH, (10, 0)),
                worker(WORKER_SIXTH, (11, 0)),
                vanguard((3, 3)),
                vanguard((4, 3), VANGUARD_TWO_ID),
                vanguard((5, 3), VANGUARD_THREE_ID),
                ranger((3, 4)),
                ranger((4, 4), RANGER_TWO_ID),
                ranger((5, 4), RANGER_THREE_ID),
            ),
            resources=15,
        )

        SmartTactic(TacticMemory()).choose_actions(turn)

        self.assertIsInstance(turn.plan.core_action, SpawnAction)
        self.assertEqual(turn.plan.core_action.unit_type, UnitType.WORKER)

    def test_core_worker_expansion_preserves_defense_reserve(self) -> None:
        turn, _ = make_turn(
            own_core=core((0, 0)),
            units=(
                worker(WORKER_LOW, (6, 0)),
                worker(WORKER_HIGH, (7, 0)),
                worker(WORKER_THIRD, (8, 0)),
                worker(WORKER_FOURTH, (9, 0)),
                worker(WORKER_FIFTH, (10, 0)),
                worker(WORKER_SIXTH, (11, 0)),
                vanguard((3, 3)),
                vanguard((4, 3), VANGUARD_TWO_ID),
                vanguard((5, 3), VANGUARD_THREE_ID),
                ranger((3, 4)),
                ranger((4, 4), RANGER_TWO_ID),
                ranger((5, 4), RANGER_THREE_ID),
            ),
            resources=14,
        )

        SmartTactic(TacticMemory()).choose_actions(turn)

        self.assertNotIsInstance(turn.plan.core_action, SpawnAction)

    def test_core_does_not_expand_workers_during_near_threat(self) -> None:
        turn, _ = make_turn(
            own_core=core((0, 0)),
            units=(
                worker(WORKER_LOW, (6, 0)),
                worker(WORKER_HIGH, (7, 0)),
                worker(WORKER_THIRD, (8, 0)),
                worker(WORKER_FOURTH, (9, 0)),
                worker(WORKER_FIFTH, (10, 0)),
                worker(WORKER_SIXTH, (11, 0)),
                vanguard((3, 3)),
                vanguard((4, 3), VANGUARD_TWO_ID),
                vanguard((5, 3), VANGUARD_THREE_ID),
                ranger((3, 4)),
                ranger((4, 4), RANGER_TWO_ID),
                ranger((5, 4), RANGER_THREE_ID),
            ),
            enemies=(enemy_core((0, 5)),),
            resources=30,
        )

        SmartTactic(TacticMemory()).choose_actions(turn)

        self.assertIsInstance(turn.plan.core_action, SpawnAction)
        self.assertEqual(turn.plan.core_action.unit_type, UnitType.RANGER)

    def test_core_adds_fourth_ranger_after_economy_and_base_defense(self) -> None:
        turn, _ = make_turn(
            own_core=core((0, 0)),
            units=(
                worker(WORKER_LOW, (6, 0)),
                worker(WORKER_HIGH, (7, 0)),
                worker(WORKER_THIRD, (8, 0)),
                worker(WORKER_FOURTH, (9, 0)),
                worker(WORKER_FIFTH, (10, 0)),
                worker(WORKER_SIXTH, (11, 0)),
                worker(WORKER_SEVENTH, (12, 0)),
                worker(WORKER_EIGHTH, (13, 0)),
                vanguard((3, 3)),
                vanguard((4, 3), VANGUARD_TWO_ID),
                vanguard((5, 3), VANGUARD_THREE_ID),
                ranger((3, 4)),
                ranger((4, 4), RANGER_TWO_ID),
                ranger((5, 4), RANGER_THREE_ID),
            ),
            resources=22,
        )

        SmartTactic(TacticMemory()).choose_actions(turn)

        self.assertIsInstance(turn.plan.core_action, SpawnAction)
        self.assertEqual(turn.plan.core_action.unit_type, UnitType.RANGER)

    def test_core_adds_fourth_vanguard_after_fourth_ranger(self) -> None:
        turn, _ = make_turn(
            own_core=core((0, 0)),
            units=(
                worker(WORKER_LOW, (6, 0)),
                worker(WORKER_HIGH, (7, 0)),
                worker(WORKER_THIRD, (8, 0)),
                worker(WORKER_FOURTH, (9, 0)),
                worker(WORKER_FIFTH, (10, 0)),
                worker(WORKER_SIXTH, (11, 0)),
                worker(WORKER_SEVENTH, (12, 0)),
                worker(WORKER_EIGHTH, (13, 0)),
                vanguard((3, 3)),
                vanguard((4, 3), VANGUARD_TWO_ID),
                vanguard((5, 3), VANGUARD_THREE_ID),
                ranger((3, 4)),
                ranger((4, 4), RANGER_TWO_ID),
                ranger((5, 4), RANGER_THREE_ID),
                ranger((6, 4), RANGER_FOURTH_ID),
            ),
            resources=20,
        )

        SmartTactic(TacticMemory()).choose_actions(turn)

        self.assertIsInstance(turn.plan.core_action, SpawnAction)
        self.assertEqual(turn.plan.core_action.unit_type, UnitType.VANGUARD)

    def test_core_keeps_reserve_before_fourth_ranger(self) -> None:
        turn, _ = make_turn(
            own_core=core((0, 0)),
            units=(
                worker(WORKER_LOW, (6, 0)),
                worker(WORKER_HIGH, (7, 0)),
                worker(WORKER_THIRD, (8, 0)),
                worker(WORKER_FOURTH, (9, 0)),
                worker(WORKER_FIFTH, (10, 0)),
                worker(WORKER_SIXTH, (11, 0)),
                worker(WORKER_SEVENTH, (12, 0)),
                worker(WORKER_EIGHTH, (13, 0)),
                vanguard((3, 3)),
                vanguard((4, 3), VANGUARD_TWO_ID),
                vanguard((5, 3), VANGUARD_THREE_ID),
                ranger((3, 4)),
                ranger((4, 4), RANGER_TWO_ID),
                ranger((5, 4), RANGER_THREE_ID),
            ),
            resources=21,
        )

        SmartTactic(TacticMemory()).choose_actions(turn)

        self.assertNotIsInstance(turn.plan.core_action, SpawnAction)

    def test_core_stops_expansion_at_population_sixteen(self) -> None:
        turn, _ = make_turn(
            own_core=core((0, 0)),
            units=(
                worker(WORKER_LOW, (6, 0)),
                worker(WORKER_HIGH, (7, 0)),
                worker(WORKER_THIRD, (8, 0)),
                worker(WORKER_FOURTH, (9, 0)),
                worker(WORKER_FIFTH, (10, 0)),
                worker(WORKER_SIXTH, (11, 0)),
                worker(WORKER_SEVENTH, (12, 0)),
                worker(WORKER_EIGHTH, (13, 0)),
                vanguard((3, 3)),
                vanguard((4, 3), VANGUARD_TWO_ID),
                vanguard((5, 3), VANGUARD_THREE_ID),
                vanguard((6, 3), VANGUARD_FOURTH_ID),
                ranger((3, 4)),
                ranger((4, 4), RANGER_TWO_ID),
                ranger((5, 4), RANGER_THREE_ID),
                ranger((6, 4), RANGER_FOURTH_ID),
            ),
            resources=30,
        )

        SmartTactic(TacticMemory()).choose_actions(turn)

        self.assertNotIsInstance(turn.plan.core_action, SpawnAction)

    def test_memory_learns_failed_terrain_destination(self) -> None:
        memory = TacticMemory(
            planned_moves={str(WORKER_LOW): PlannedMove(destination=(1, 0), tick=7)}
        )
        event = ResolutionEvent(
            event_id=UUID("00000000-0000-4000-8000-000000000302"),
            tick=7,
            event_type="UNIT_MOVE_FAILED",
            reason_code="MOVE_BLOCKED_TERRAIN",
            actor_id=WORKER_LOW,
            position=(0, 0),
        )
        turn, _ = make_turn(
            tick=8,
            own_core=core(),
            units=(worker(WORKER_LOW, (0, 0)),),
            resource_cells=((2, 0),),
            events=(event,),
        )

        SmartTactic(memory).choose_actions(turn)

        self.assertIn((1, 0), memory.known_obstacles)
        action = turn.plan.unit_actions[WORKER_LOW]
        self.assertIsInstance(action, MoveAction)
        self.assertNotEqual(action.direction, Direction.RIGHT)

    def test_memory_detects_manual_move_override(self) -> None:
        memory = TacticMemory(
            planned_moves={str(WORKER_LOW): PlannedMove(destination=(1, 0), tick=7)}
        )
        event = ResolutionEvent(
            event_id=UUID("00000000-0000-4000-8000-000000000303"),
            tick=7,
            event_type="UNIT_MOVE_SUCCEEDED",
            actor_id=WORKER_LOW,
            position=(0, 1),
        )
        turn, _ = make_turn(
            tick=8,
            own_core=core(),
            units=(worker(WORKER_LOW, (0, 1)),),
            events=(event,),
        )

        summary = SmartTactic(memory).choose_actions(turn)

        self.assertEqual(memory.decision_totals["manual_override:move"], 1)
        self.assertTrue(any("manual_override" in item for item in summary.decisions))

    def test_worker_keeps_last_seen_resource_goal_across_turns(self) -> None:
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        first, _ = make_turn(
            tick=10,
            own_core=core((20, 20)),
            units=(worker(WORKER_LOW, (0, 0)), ranger((5, 0))),
            resource_cells=((5, 0),),
        )
        second, _ = make_turn(
            tick=11,
            own_core=core((20, 20)),
            units=(worker(WORKER_LOW, (1, 0)), ranger((20, 19))),
        )

        tactic.choose_actions(first)
        tactic.choose_actions(second)

        action = second.plan.unit_actions[WORKER_LOW]
        self.assertIsInstance(action, MoveAction)
        self.assertEqual(action.direction, Direction.RIGHT)
        self.assertEqual(memory.worker_goals[str(WORKER_LOW)].position, (5, 0))

    def test_visible_absent_resource_hint_is_invalidated_immediately(self) -> None:
        memory = TacticMemory(resource_last_seen={(2, 0): 7})
        turn, _ = make_turn(
            tick=8,
            own_core=core((10, 10)),
            units=(worker(WORKER_LOW, (0, 0)),),
        )

        summary = SmartTactic(memory).choose_actions(turn)

        self.assertNotIn((2, 0), memory.resource_last_seen)
        self.assertTrue(any("resource_invalidated" in item for item in summary.decisions))

    def test_obstacle_blocked_resource_hint_remains_uncertain(self) -> None:
        memory = TacticMemory(resource_last_seen={(2, 0): 7})
        turn, _ = make_turn(
            tick=8,
            own_core=core((10, 10)),
            units=(worker(WORKER_LOW, (0, 0)),),
            obstacle_cells=((1, 0),),
        )

        SmartTactic(memory).choose_actions(turn)

        self.assertIn((2, 0), memory.resource_last_seen)

    def test_worker_closes_move_then_harvest_loop(self) -> None:
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        approach, _ = make_turn(
            tick=30,
            own_core=core(),
            units=(worker(WORKER_LOW, (0, 0)),),
            resource_cells=((1, 0),),
        )
        arrived, _ = make_turn(
            tick=31,
            own_core=core(),
            units=(worker(WORKER_LOW, (1, 0)),),
            resource_cells=((1, 0),),
        )

        tactic.choose_actions(approach)
        tactic.choose_actions(arrived)

        self.assertIsInstance(approach.plan.unit_actions[WORKER_LOW], MoveAction)
        self.assertEqual(approach.plan.unit_actions[WORKER_LOW].direction, Direction.RIGHT)
        self.assertIsInstance(arrived.plan.unit_actions[WORKER_LOW], HarvestAction)

    def test_worker_does_not_switch_an_existing_visible_resource_goal(self) -> None:
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        first, _ = make_turn(
            tick=40,
            own_core=core(),
            units=(worker(WORKER_LOW, (0, 0)),),
            resource_cells=((3, 0), (0, 4)),
        )
        second, _ = make_turn(
            tick=41,
            own_core=core(),
            units=(worker(WORKER_LOW, (1, 0)),),
            resource_cells=((3, 0), (1, 1)),
        )

        tactic.choose_actions(first)
        tactic.choose_actions(second)

        self.assertEqual(memory.worker_goals[str(WORKER_LOW)].position, (3, 0))
        action = second.plan.unit_actions[WORKER_LOW]
        self.assertIsInstance(action, MoveAction)
        self.assertEqual(action.direction, Direction.RIGHT)

    def test_worker_keeps_visible_resource_goal_after_it_leaves_vision(self) -> None:
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        first, _ = make_turn(
            tick=40,
            own_core=core((20, 20)),
            units=(worker(WORKER_LOW, (0, 0)),),
            resource_cells=((3, 0),),
        )
        second, _ = make_turn(
            tick=41,
            own_core=core((20, 20)),
            units=(worker(WORKER_LOW, (-1, 0)),),
            resource_cells=((-1, 1),),
        )

        tactic.choose_actions(first)
        tactic.choose_actions(second)

        goal = memory.worker_goals[str(WORKER_LOW)]
        self.assertEqual(goal.kind, "visible_resource")
        self.assertEqual(goal.position, (3, 0))
        action = second.plan.unit_actions[WORKER_LOW]
        self.assertIsInstance(action, MoveAction)
        self.assertEqual(action.direction, Direction.RIGHT)

    def test_one_worker_preempts_frontier_for_configured_resource_recovery(self) -> None:
        memory = TacticMemory(
            recovery_targets=[(10, 0)],
            worker_goals={
                str(WORKER_LOW): WorkerGoal("frontier", (0, -8), 7),
                str(WORKER_HIGH): WorkerGoal("frontier", (20, -8), 7),
            },
        )
        turn, _ = make_turn(
            own_core=core((20, 20)),
            units=(worker(WORKER_LOW, (0, 0)), worker(WORKER_HIGH, (20, 0))),
        )

        summary = SmartTactic(memory).choose_actions(turn)

        recovery_goals = [
            (unit_id, goal)
            for unit_id, goal in memory.worker_goals.items()
            if goal.kind == "resource_recovery"
        ]
        self.assertEqual(len(recovery_goals), 1)
        unit_id, goal = recovery_goals[0]
        self.assertEqual(unit_id, str(WORKER_LOW))
        self.assertEqual(goal.position, (10, 0))
        action = turn.plan.unit_actions[WORKER_LOW]
        self.assertIsInstance(action, MoveAction)
        self.assertEqual(action.direction, Direction.RIGHT)
        self.assertTrue(any("resource_recovery_assigned" in item for item in summary.decisions))

    def test_eight_workers_allow_two_resource_recovery_scouts(self) -> None:
        memory = TacticMemory(
            recovery_targets=[(40, 0), (40, 5), (40, 10), (40, 15)]
        )
        turn, _ = make_turn(
            own_core=core((20, 20)),
            units=(
                worker(WORKER_LOW, (0, 0)),
                worker(WORKER_HIGH, (0, 5)),
                worker(WORKER_THIRD, (0, 10)),
                worker(WORKER_FOURTH, (0, 15)),
                worker(WORKER_FIFTH, (5, 0)),
                worker(WORKER_SIXTH, (5, 5)),
                worker(WORKER_SEVENTH, (5, 10)),
                worker(WORKER_EIGHTH, (5, 15)),
            ),
        )

        SmartTactic(memory).choose_actions(turn)

        recovery_goals = [
            goal for goal in memory.worker_goals.values()
            if goal.kind == "resource_recovery"
        ]
        self.assertEqual(len(recovery_goals), 2)

    def test_visible_absent_resource_recovery_target_is_checked_once(self) -> None:
        memory = TacticMemory(recovery_targets=[(2, 0)])
        turn, _ = make_turn(
            own_core=core((10, 10)),
            units=(worker(WORKER_LOW, (0, 0)),),
        )

        summary = SmartTactic(memory).choose_actions(turn)

        self.assertNotIn((2, 0), memory.recovery_targets)
        self.assertIn((2, 0), memory.recovery_checked)
        self.assertTrue(any("result=visible_absent" in item for item in summary.decisions))

    def test_core_does_not_follow_resource_recovery_scout(self) -> None:
        memory = TacticMemory(
            recovery_targets=[(-10, 0)],
            worker_goals={
                str(WORKER_LOW): WorkerGoal("resource_recovery", (-10, 0), 7),
                str(WORKER_HIGH): WorkerGoal("frontier", (10, 0), 7),
            },
        )
        turn, _ = make_turn(
            own_core=core((0, 0)),
            units=(worker(WORKER_LOW, (-1, 0)), worker(WORKER_HIGH, (1, 0))),
            beacon=ChampionBeacon(
                position=(0, 0),
                status=BeaconStatus.CARRIED,
                carrier_id=CORE_ID,
            ),
        )

        SmartTactic(memory).choose_actions(turn)

        self.assertIsInstance(turn.plan.core_action, StartMoveAction)
        self.assertEqual(turn.plan.core_action.direction, Direction.RIGHT)

    def test_workers_receive_distinct_nearest_resource_assignments(self) -> None:
        turn, _ = make_turn(
            own_core=core((5, 5)),
            units=(worker(WORKER_LOW, (0, 0)), worker(WORKER_HIGH, (10, 0))),
            resource_cells=((1, 0), (9, 0)),
        )

        SmartTactic(TacticMemory()).choose_actions(turn)

        low_action = turn.plan.unit_actions[WORKER_LOW]
        high_action = turn.plan.unit_actions[WORKER_HIGH]
        self.assertIsInstance(low_action, MoveAction)
        self.assertIsInstance(high_action, MoveAction)
        self.assertEqual(low_action.direction, Direction.RIGHT)
        self.assertEqual(high_action.direction, Direction.LEFT)

    def test_two_rangers_focus_fire_to_reach_lethal_damage(self) -> None:
        turn, _ = make_turn(
            own_core=core(),
            units=(ranger((0, 0)), ranger((0, 1), RANGER_TWO_ID)),
            enemies=(enemy_ranger((0, 3), hp=2),),
        )

        SmartTactic(TacticMemory()).choose_actions(turn)

        first_action = turn.plan.unit_actions[RANGER_ID]
        second_action = turn.plan.unit_actions[RANGER_TWO_ID]
        self.assertIsInstance(first_action, ShootAction)
        self.assertIsInstance(second_action, ShootAction)
        self.assertEqual(first_action.target_id, ENEMY_RANGER_ID)
        self.assertEqual(second_action.target_id, ENEMY_RANGER_ID)

    def test_worker_deposit_can_fund_same_tick_core_repair(self) -> None:
        turn, _ = make_turn(
            own_core=core((0, 0), shield=4),
            units=(worker(WORKER_LOW, (0, 0), cargo=1),),
            enemies=(enemy_core((0, 3)),),
            resources=0,
        )

        SmartTactic(TacticMemory()).choose_actions(turn)

        self.assertIsInstance(turn.plan.unit_actions[WORKER_LOW], DepositAction)
        self.assertIsInstance(turn.plan.core_action, RepairShieldAction)

    def test_core_cell_defender_vacates_so_cargo_worker_can_enter(self) -> None:
        turn, _ = make_turn(
            own_core=core((0, 0)),
            units=(
                ranger((0, 0)),
                worker(WORKER_LOW, (1, 0), cargo=1),
            ),
            beacon=ChampionBeacon(position=(10, -10)),
        )

        summary = SmartTactic(TacticMemory()).choose_actions(turn)

        ranger_action = turn.plan.unit_actions[RANGER_ID]
        worker_action = turn.plan.unit_actions[WORKER_LOW]
        self.assertIsInstance(ranger_action, MoveAction)
        self.assertIsInstance(worker_action, MoveAction)
        self.assertEqual(worker_action.direction, Direction.LEFT)
        self.assertTrue(
            any("core_logistics_space" in item for item in summary.decisions)
        )

    def test_core_migrates_toward_worker_frontier_when_no_cargo_is_near(self) -> None:
        memory = TacticMemory(
            worker_goals={
                str(WORKER_LOW): WorkerGoal("frontier", (10, 0), 8),
            }
        )
        turn, _ = make_turn(
            tick=8,
            own_core=core((0, 0)),
            units=(worker(WORKER_LOW, (6, 0)),),
        )

        SmartTactic(memory).choose_actions(turn)

        self.assertIsInstance(turn.plan.core_action, StartMoveAction)
        self.assertEqual(turn.plan.core_action.direction, Direction.RIGHT)
        self.assertEqual(memory.core_heading, Direction.RIGHT)
        self.assertEqual(memory.last_core_move_tick, 8)

    def test_cargo_worker_at_exactly_five_cells_blocks_core_migration(self) -> None:
        turn, _ = make_turn(
            own_core=core((0, 0)),
            units=(worker(WORKER_LOW, (5, 0), cargo=1),),
        )

        SmartTactic(TacticMemory()).choose_actions(turn)

        self.assertIsNone(turn.plan.core_action)

    def test_core_migrates_toward_distant_cargo_worker(self) -> None:
        turn, _ = make_turn(
            own_core=core((0, 0)),
            units=(worker(WORKER_LOW, (6, 0), cargo=1),),
        )

        summary = SmartTactic(TacticMemory()).choose_actions(turn)

        self.assertIsInstance(turn.plan.core_action, StartMoveAction)
        self.assertEqual(turn.plan.core_action.direction, Direction.RIGHT)
        self.assertTrue(any("reason=rendezvous_cargo" in item for item in summary.decisions))

    def test_core_waits_when_only_legal_step_moves_away_from_beacon(self) -> None:
        turn, _ = make_turn(
            own_core=core((0, 0)),
            units=(worker(WORKER_LOW, (6, 0), cargo=1),),
            obstacle_cells=((1, 0), (0, -1), (0, 1)),
            beacon=ChampionBeacon(position=(10, 0)),
        )

        SmartTactic(TacticMemory()).choose_actions(turn)

        self.assertIsNone(turn.plan.core_action)

    def test_core_beacon_bias_overrides_distant_cargo_behind(self) -> None:
        turn, _ = make_turn(
            own_core=core((0, 0)),
            units=(worker(WORKER_LOW, (0, 6), cargo=1),),
            beacon=ChampionBeacon(position=(10, -10)),
        )

        summary = SmartTactic(TacticMemory()).choose_actions(turn)

        self.assertIsInstance(turn.plan.core_action, StartMoveAction)
        self.assertIn(
            turn.plan.core_action.direction,
            {Direction.UP, Direction.RIGHT},
        )
        self.assertTrue(
            any("beacon=(10, -10)" in item for item in summary.decisions)
        )

    def test_core_does_not_immediately_reverse_for_multiple_distant_cargo_workers(
        self,
    ) -> None:
        memory = TacticMemory(
            core_heading=Direction.UP,
            last_core_move_tick=4,
        )
        turn, _ = make_turn(
            tick=8,
            own_core=core((0, 0)),
            units=(
                worker(WORKER_LOW, (0, 9), cargo=1),
                worker(WORKER_HIGH, (1, 9), cargo=1),
            ),
            beacon=ChampionBeacon(position=(0, -20)),
        )

        SmartTactic(memory).choose_actions(turn)

        self.assertIsInstance(turn.plan.core_action, StartMoveAction)
        self.assertEqual(turn.plan.core_action.direction, Direction.UP)

    def test_enemy_within_eight_cells_blocks_core_migration(self) -> None:
        memory = TacticMemory(
            worker_goals={
                str(WORKER_LOW): WorkerGoal("frontier", (10, 0), 8),
            }
        )
        turn, _ = make_turn(
            tick=8,
            own_core=core((0, 0)),
            units=(worker(WORKER_LOW, (6, 0)),),
            enemies=(enemy_core((0, 8)),),
        )

        SmartTactic(memory).choose_actions(turn)

        self.assertIsNone(turn.plan.core_action)

    def test_cargo_worker_on_moving_core_heads_to_core_destination(self) -> None:
        turn, _ = make_turn(
            own_core=moving_core((0, 0), direction=Direction.RIGHT),
            units=(worker(WORKER_LOW, (0, 0), cargo=1),),
        )

        summary = SmartTactic(TacticMemory()).choose_actions(turn)

        action = turn.plan.unit_actions[WORKER_LOW]
        self.assertIsInstance(action, MoveAction)
        self.assertEqual(action.direction, Direction.RIGHT)
        self.assertTrue(
            any("reason=rendezvous_moving_core" in item for item in summary.decisions)
        )

    def test_memory_round_trip_preserves_learning(self) -> None:
        memory = TacticMemory(
            known_obstacles={(1, 2)},
            resource_last_seen={(3, 4): 20},
            recovery_targets=[(11, 12)],
            recovery_checked={(13, 14)},
            temporary_blocks={(5, 6): 22},
            planned_moves={str(WORKER_LOW): PlannedMove((7, 8), 21)},
            unit_labels={str(WORKER_LOW): UnitLabel("WORKER", 4)},
            unit_label_counters={"WORKER": 4},
            core_heading=Direction.LEFT,
            last_core_move_tick=19,
            last_tick=21,
        )
        memory.visited[(9, 10)] = 3

        with TemporaryDirectory() as directory:
            path = Path(directory) / "memory.json"
            memory.save(path)
            restored = TacticMemory.load(path)

        self.assertEqual(restored.known_obstacles, {(1, 2)})
        self.assertEqual(restored.resource_last_seen[(3, 4)], 20)
        self.assertEqual(restored.recovery_targets, [(11, 12)])
        self.assertEqual(restored.recovery_checked, {(13, 14)})
        self.assertEqual(restored.temporary_blocks[(5, 6)], 22)
        self.assertEqual(restored.visited[(9, 10)], 3)
        self.assertEqual(restored.planned_moves[str(WORKER_LOW)].destination, (7, 8))
        self.assertEqual(restored.unit_labels[str(WORKER_LOW)], UnitLabel("WORKER", 4))
        self.assertEqual(restored.unit_label_counters["WORKER"], 4)
        self.assertEqual(restored.core_heading, Direction.LEFT)
        self.assertEqual(restored.last_core_move_tick, 19)

    def test_memory_load_merges_unchecked_recovery_hint_file(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "memory.json"
            TacticMemory(recovery_checked={(3, 4)}).save(path)
            path.with_name(".arena_hero_recovery_targets.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "targets": [[1, 2], [3, 4], [1, 2]],
                    }
                ),
                encoding="utf-8",
            )

            restored = TacticMemory.load(path)

        self.assertEqual(restored.recovery_targets, [(1, 2)])
        self.assertEqual(restored.recovery_checked, {(3, 4)})

    def test_chunk_math_matches_negative_coordinate_contract(self) -> None:
        self.assertEqual(_chunk_of((-1, -1)), (-1, -1))
        self.assertEqual(_chunk_of((-32, 31)), (-1, 0))
        self.assertEqual(_chunk_quota((-1, 0)), 16)
        self.assertEqual(_refill_tick_at_or_after(12), 12)
        self.assertEqual(_refill_tick_at_or_after(13), 16)

    def test_harvest_records_productive_chunk_and_refill_tick(self) -> None:
        memory = TacticMemory()
        event = ResolutionEvent(
            event_id=UUID("00000000-0000-4000-8000-000000000304"),
            tick=12,
            event_type="HARVEST_SUCCEEDED",
            actor_id=WORKER_LOW,
            position=(-31, 89),
            values={"amount": 1, "source": "RESOURCE_NODE"},
        )
        turn, _ = make_turn(
            tick=13,
            own_core=core(),
            units=(worker(WORKER_LOW, (-30, 89), cargo=1),),
            events=(event,),
        )

        summary = SmartTactic(memory).choose_actions(turn)

        chunk = _chunk_of((-31, 89))
        self.assertEqual(memory.chunk_harvests[chunk], 1)
        self.assertEqual(memory.chunk_next_refill[chunk], 12)
        self.assertEqual(memory.chunk_anchors[chunk], (-31, 89))
        self.assertTrue(
            any(
                "harvest_result source=RESOURCE_NODE amount=1" in item
                for item in summary.decisions
            )
        )

    def test_old_exact_resource_hint_yields_to_frontier_exploration(self) -> None:
        memory = TacticMemory(resource_last_seen={(20, 20): 1})
        turn, _ = make_turn(
            tick=20,
            own_core=core((0, 0)),
            units=(worker(WORKER_LOW, (1, 0)),),
        )

        summary = SmartTactic(memory).choose_actions(turn)

        self.assertFalse(any("last_seen_resource" in item for item in summary.decisions))
        self.assertEqual(memory.worker_goals[str(WORKER_LOW)].kind, "frontier")

    def test_frontier_exploration_reduces_beacon_distance(self) -> None:
        memory = TacticMemory()
        beacon_position = (20, -20)
        turn, _ = make_turn(
            own_core=core((0, 0)),
            units=(worker(WORKER_LOW, (1, 0)),),
            beacon=ChampionBeacon(position=beacon_position),
        )

        SmartTactic(memory).choose_actions(turn)

        goal = memory.worker_goals[str(WORKER_LOW)]
        core_distance = abs(beacon_position[0]) + abs(beacon_position[1])
        goal_distance = abs(goal.position[0] - beacon_position[0]) + abs(
            goal.position[1] - beacon_position[1]
        )
        self.assertEqual(goal.kind, "frontier")
        self.assertLess(goal_distance, core_distance)

    def test_long_backward_refill_probe_is_replaced_by_beacon_frontier(self) -> None:
        memory = TacticMemory(
            worker_goals={
                str(WORKER_LOW): WorkerGoal("refilled_chunk", (-24, 0), 8),
            },
        )
        beacon_position = (20, 0)
        turn, _ = make_turn(
            tick=12,
            own_core=core((0, 0)),
            units=(worker(WORKER_LOW, (1, 0)),),
            beacon=ChampionBeacon(position=beacon_position),
        )

        summary = SmartTactic(memory).choose_actions(turn)

        goal = memory.worker_goals[str(WORKER_LOW)]
        self.assertNotEqual(goal.kind, "refilled_chunk")
        self.assertLess(
            abs(goal.position[0] - beacon_position[0])
            + abs(goal.position[1] - beacon_position[1]),
            abs(beacon_position[0]),
        )
        self.assertTrue(
            any("refill_probe_strategic_trimmed" in item for item in summary.decisions)
        )

    def test_owned_beacon_trims_refill_probe_far_from_core(self) -> None:
        memory = TacticMemory(
            worker_goals={
                str(WORKER_LOW): WorkerGoal("refilled_chunk", (30, 0), 8),
            },
        )
        turn, _ = make_turn(
            tick=12,
            own_core=core((0, 0)),
            units=(worker(WORKER_LOW, (1, 0)),),
            beacon=ChampionBeacon(
                position=(0, 0),
                status=BeaconStatus.CARRIED,
                carrier_id=CORE_ID,
            ),
        )

        summary = SmartTactic(memory).choose_actions(turn)

        goal = memory.worker_goals[str(WORKER_LOW)]
        self.assertNotEqual(goal.kind, "refilled_chunk")
        self.assertLessEqual(abs(goal.position[0]) + abs(goal.position[1]), 11)
        self.assertTrue(
            any("refill_probe_strategic_trimmed" in item for item in summary.decisions)
        )

    def test_long_backward_last_seen_resource_is_replaced_by_beacon_frontier(
        self,
    ) -> None:
        memory = TacticMemory(
            resource_last_seen={(-20, 0): 12},
            worker_goals={
                str(WORKER_LOW): WorkerGoal("last_seen_resource", (-20, 0), 12),
            },
        )
        beacon_position = (20, 0)
        turn, _ = make_turn(
            tick=12,
            own_core=core((0, 0)),
            units=(worker(WORKER_LOW, (1, 0)),),
            beacon=ChampionBeacon(position=beacon_position),
        )

        summary = SmartTactic(memory).choose_actions(turn)

        goal = memory.worker_goals[str(WORKER_LOW)]
        self.assertEqual(goal.kind, "frontier")
        self.assertLess(
            abs(goal.position[0] - beacon_position[0])
            + abs(goal.position[1] - beacon_position[1]),
            abs(beacon_position[0]),
        )
        self.assertTrue(
            any(
                "last_seen_resource_strategic_trimmed" in item
                for item in summary.decisions
            )
        )

    def test_due_productive_chunk_gets_a_probe_assignment(self) -> None:
        memory = TacticMemory(
            chunk_harvests={(0, 0): 2},
            chunk_next_refill={(0, 0): 8},
            chunk_anchors={(0, 0): (10, 10)},
        )
        turn, _ = make_turn(
            tick=12,
            own_core=core((0, 0)),
            units=(worker(WORKER_LOW, (1, 0)),),
        )

        SmartTactic(memory).choose_actions(turn)

        self.assertEqual(memory.worker_goals[str(WORKER_LOW)].kind, "refilled_chunk")
        self.assertEqual(_chunk_of(memory.worker_goals[str(WORKER_LOW)].position), (0, 0))

    def test_refill_probe_tries_an_alternate_strategic_point(self) -> None:
        memory = TacticMemory(
            chunk_harvests={(0, 0): 2},
            chunk_next_refill={(0, 0): 8},
            chunk_anchors={(0, 0): (10, 10)},
        )
        turn, _ = make_turn(
            tick=12,
            own_core=core((0, 0)),
            units=(worker(WORKER_LOW, (1, 0)),),
            beacon=ChampionBeacon(position=(20, 0)),
        )

        SmartTactic(memory).choose_actions(turn)

        goal = memory.worker_goals[str(WORKER_LOW)]
        self.assertEqual(goal.kind, "refilled_chunk")
        self.assertEqual(goal.position, (24, 8))

    def test_refilled_chunk_probe_concurrency_is_bounded(self) -> None:
        memory = TacticMemory(
            chunk_harvests={(0, 0): 2},
            chunk_next_refill={(0, 0): 8},
            chunk_anchors={(0, 0): (10, 10)},
            worker_goals={
                str(WORKER_LOW): WorkerGoal("refilled_chunk", (24, 24), 9),
                str(WORKER_HIGH): WorkerGoal("refilled_chunk", (8, 8), 10),
            },
        )
        turn, _ = make_turn(
            tick=12,
            own_core=core((0, 0)),
            units=(worker(WORKER_LOW, (3, 0)), worker(WORKER_HIGH, (4, 0))),
        )

        summary = SmartTactic(memory).choose_actions(turn)

        active_probes = [
            goal for goal in memory.worker_goals.values() if goal.kind == "refilled_chunk"
        ]
        self.assertEqual(len(active_probes), 1)
        self.assertTrue(
            any(
                "refill_probe" in item and "trimmed" in item
                for item in summary.decisions
            )
        )


class ModeAndRecallTests(unittest.TestCase):
    """发育/侵略双模式 + 一键召回 + stats 写入。"""

    def _write_control(
        self,
        path: Path,
        *,
        mode: str | None = None,
        recall: bool | None = None,
    ) -> None:
        data: dict = {}
        if mode is not None:
            data["mode"] = mode
        if recall is not None:
            data["recall"] = recall
        path.write_text(json.dumps(data), encoding="utf-8")

    def test_aggress_mode_spawns_rangers_over_workers(self) -> None:
        with TemporaryDirectory() as directory:
            control_path = Path(directory) / ".arena_hero_control.json"
            self._write_control(control_path, mode="aggress")
            turn, _ = make_turn(
                own_core=core((0, 0)),
                units=(
                    worker(WORKER_LOW, (6, 0)),
                    worker(WORKER_HIGH, (7, 0)),
                    worker(WORKER_THIRD, (8, 0)),
                    worker(WORKER_FOURTH, (9, 0)),
                    vanguard((3, 3)),
                    vanguard((4, 3), VANGUARD_TWO_ID),
                    ranger((3, 4)),
                    ranger((4, 4), RANGER_TWO_ID),
                ),
                resources=30,
            )
            SmartTactic(TacticMemory(), control_path=control_path).choose_actions(turn)

            self.assertIsInstance(turn.plan.core_action, SpawnAction)
            self.assertEqual(turn.plan.core_action.unit_type, UnitType.RANGER)

    def test_develop_mode_prioritizes_workers(self) -> None:
        with TemporaryDirectory() as directory:
            control_path = Path(directory) / ".arena_hero_control.json"
            self._write_control(control_path, mode="develop")
            turn, _ = make_turn(
                own_core=core((0, 0)),
                units=(
                    worker(WORKER_LOW, (6, 0)),
                    worker(WORKER_HIGH, (7, 0)),
                    worker(WORKER_THIRD, (8, 0)),
                    worker(WORKER_FOURTH, (9, 0)),
                    vanguard((3, 3)),
                    vanguard((4, 3), VANGUARD_TWO_ID),
                    ranger((3, 4)),
                    ranger((4, 4), RANGER_TWO_ID),
                ),
                resources=30,
            )
            SmartTactic(TacticMemory(), control_path=control_path).choose_actions(turn)

            self.assertIsInstance(turn.plan.core_action, SpawnAction)
            self.assertEqual(turn.plan.core_action.unit_type, UnitType.WORKER)

    def test_aggress_vanguard_advances_toward_beacon_when_no_enemies(self) -> None:
        with TemporaryDirectory() as directory:
            control_path = Path(directory) / ".arena_hero_control.json"
            self._write_control(control_path, mode="aggress")
            turn, _ = make_turn(
                own_core=core((5, 5)),
                units=(vanguard((10, 10)),),
            )
            SmartTactic(TacticMemory(), control_path=control_path).choose_actions(turn)

            action = turn.plan.unit_actions.get(VANGUARD_ID)
            self.assertIsInstance(action, MoveAction)

    def test_aggress_ranger_advances_toward_beacon_when_no_enemies(self) -> None:
        with TemporaryDirectory() as directory:
            control_path = Path(directory) / ".arena_hero_control.json"
            self._write_control(control_path, mode="aggress")
            turn, _ = make_turn(
                own_core=core((5, 5)),
                units=(ranger((10, 10)),),
            )
            SmartTactic(TacticMemory(), control_path=control_path).choose_actions(turn)

            action = turn.plan.unit_actions.get(RANGER_ID)
            self.assertIsInstance(action, MoveAction)

    def test_recall_vanguards_return_to_core(self) -> None:
        with TemporaryDirectory() as directory:
            control_path = Path(directory) / ".arena_hero_control.json"
            self._write_control(control_path, recall=True)
            turn, _ = make_turn(
                own_core=core((5, 5)),
                units=(vanguard((20, 20)), ranger((21, 21))),
            )
            SmartTactic(TacticMemory(), control_path=control_path).choose_actions(turn)

            action = turn.plan.unit_actions.get(VANGUARD_ID)
            self.assertIsInstance(action, MoveAction)

    def test_recall_rangers_return_to_patrol(self) -> None:
        with TemporaryDirectory() as directory:
            control_path = Path(directory) / ".arena_hero_control.json"
            self._write_control(control_path, recall=True)
            turn, _ = make_turn(
                own_core=core((5, 5)),
                units=(ranger((21, 21)),),
            )
            SmartTactic(TacticMemory(), control_path=control_path).choose_actions(turn)

            action = turn.plan.unit_actions.get(RANGER_ID)
            self.assertIsInstance(action, MoveAction)

    def test_recall_production_prefers_defense(self) -> None:
        with TemporaryDirectory() as directory:
            control_path = Path(directory) / ".arena_hero_control.json"
            self._write_control(control_path, recall=True)
            turn, _ = make_turn(
                own_core=core((0, 0)),
                units=(
                    worker(WORKER_LOW, (6, 0)),
                    worker(WORKER_HIGH, (7, 0)),
                    worker(WORKER_THIRD, (8, 0)),
                    worker(WORKER_FOURTH, (9, 0)),
                    worker(WORKER_FIFTH, (10, 0)),
                    worker(WORKER_SIXTH, (11, 0)),
                ),
                resources=30,
            )
            SmartTactic(TacticMemory(), control_path=control_path).choose_actions(turn)

            self.assertIsInstance(turn.plan.core_action, SpawnAction)
            self.assertEqual(turn.plan.core_action.unit_type, UnitType.VANGUARD)

    def test_load_control_absent_keeps_default(self) -> None:
        memory = TacticMemory()
        memory.load_control(Path("/nonexistent/control.json"))
        self.assertEqual(memory.mode, MODE_DEVELOP)
        self.assertFalse(memory.recall)

    def test_load_control_switches_mode_and_recall(self) -> None:
        with TemporaryDirectory() as directory:
            control_path = Path(directory) / ".arena_hero_control.json"
            self._write_control(control_path, mode="aggress", recall=True)
            memory = TacticMemory()
            memory.load_control(control_path)
            self.assertEqual(memory.mode, MODE_AGGRESS)
            self.assertTrue(memory.recall)

    def test_write_stats_round_trip(self) -> None:
        memory = TacticMemory()
        turn, _ = make_turn(
            own_core=core((5, 5)),
            units=(worker(WORKER_LOW, (6, 5)),),
            resources=3,
        )
        with TemporaryDirectory() as directory:
            stats_path = Path(directory) / "stats.json"
            memory.write_stats(stats_path, turn)
            payload = json.loads(stats_path.read_text(encoding="utf-8"))

        self.assertEqual(payload["tick"], turn.tick)
        self.assertEqual(payload["workers"], 1)
        self.assertEqual(payload["vanguards"], 0)
        self.assertEqual(payload["rangers"], 0)
        self.assertEqual(payload["resources"], 3)
        self.assertEqual(payload["mode"], MODE_DEVELOP)
        self.assertFalse(payload["recall"])
        self.assertIn("total_resources_harvested", payload)
        self.assertIn("enemy_cores_destroyed", payload)

    def test_write_stats_records_cumulative_resources(self) -> None:
        memory = TacticMemory()
        harvest = ResolutionEvent(
            event_id=UUID("00000000-0000-4000-8000-000000000301"),
            tick=8,
            event_type="HARVEST_SUCCEEDED",
            actor_id=WORKER_LOW,
            position=(6, 5),
            values={"amount": 2, "source": "RESOURCE_NODE"},
        )
        deposit = ResolutionEvent(
            event_id=UUID("00000000-0000-4000-8000-000000000302"),
            tick=9,
            event_type="DEPOSIT_SUCCEEDED",
            actor_id=WORKER_LOW,
            values={"amount": 2},
        )
        turn, _ = make_turn(
            tick=9,
            own_core=core((5, 5)),
            units=(worker(WORKER_LOW, (5, 5), cargo=2),),
            resources=2,
            events=(harvest, deposit),
        )
        memory.observe(turn)
        with TemporaryDirectory() as directory:
            stats_path = Path(directory) / "stats.json"
            memory.write_stats(stats_path, turn)
            payload = json.loads(stats_path.read_text(encoding="utf-8"))

        self.assertEqual(payload["total_resources_harvested"], 2)
        self.assertEqual(payload["total_resources_deposited"], 2)


if __name__ == "__main__":
    unittest.main()
