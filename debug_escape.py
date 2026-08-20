#!/usr/bin/env python3
"""逐 tick 打印 _lightning_step_toward 的内部状态，找出为什么逃生不触发。"""

import re
from uuid import UUID

from arena_hero_strategy import (
    CORE_BEACON_HYSTERESIS,
    LIGHTNING_ESCAPE_DETECT_SPAN,
    LIGHTNING_ESCAPE_DETECT_WINDOW,
    LIGHTNING_ESCAPE_REVISIT_MIN,
    LIGHTNING_ESCAPE_TRIGGER_HITS,
    MovementPlanner,
    SmartTactic,
    TacticMemory,
    _distance,
)
from test_arena_hero_tactic import core, make_turn, ranger

POCKET = (
    [(640, y) for y in range(592, 609)]
    + [(x, 592) for x in range(640, 656)]
    + [(x, 608) for x in range(640, 656)]
    + [
        (646, 598), (647, 598), (648, 598),
        (646, 602), (647, 602), (648, 602),
        (650, 600), (651, 600),
    ]
)
START = (652, 600)
GOAL = (600, 600)
UID = str(UUID(int=0xB0B0))

memory = TacticMemory()
tactic = SmartTactic(memory)
memory.known_obstacles = set(POCKET)
for x in range(START[0] - 25, START[0] + 15):
    for y in range(START[1] - 15, START[1] + 15):
        memory.visited[(x, y)] = 25

pos = START
print(f"{'tick':>4} {'pos':>12} {'dist':>5} {'span':>4} {'revis':>5} "
      f"{'hits':>4} {'esc':>3}  moved")
print("-" * 60)

for tick in range(100, 145):
    turn, _ = make_turn(
        tick=tick,
        own_core=core((600, 600)),
        units=(ranger(pos, UUID(int=0xB0B0)),),
        obstacle_cells=tuple(POCKET),
    )
    decisions: list[str] = []
    planner = MovementPlanner(turn, memory, decisions)

    # 复刻检测逻辑的输入，打印出来
    window = memory.recent_positions.get(UID, [])[-LIGHTNING_ESCAPE_DETECT_WINDOW:]
    if len(window) >= LIGHTNING_ESCAPE_DETECT_WINDOW:
        xs = [p[0] for p in window]
        ys = [p[1] for p in window]
        span = max(max(xs) - min(xs), max(ys) - min(ys))
        revisit = window.count(pos)
    else:
        span, revisit = -1, -1
    hits_before = memory.lightning_unit_stuck_counters.get(UID, 0)
    dist = _distance(pos, GOAL)

    moved = tactic._lightning_step_toward(turn, planner, turn.rangers[0], GOAL, "dbg")
    escaping = tick < memory.lightning_unit_escape_until.get(UID, 0)

    new_pos = pos
    if moved:
        line = next((d for d in decisions if " move " in d), "")
        m = re.search(r"to=\((-?\d+), (-?\d+)\)", line)
        if m:
            new_pos = (int(m.group(1)), int(m.group(2)))

    flag = ""
    if span >= 0 and not (span <= LIGHTNING_ESCAPE_DETECT_SPAN):
        flag = f" span>{LIGHTNING_ESCAPE_DETECT_SPAN} 漏检"
    elif span >= 0 and revisit < LIGHTNING_ESCAPE_REVISIT_MIN:
        flag = f" revisit<{LIGHTNING_ESCAPE_REVISIT_MIN} 漏检"
    elif dist <= CORE_BEACON_HYSTERESIS:
        flag = " 距目标太近，检测跳过"

    print(f"{tick:>4} {str(pos):>12} {dist:>5} {span:>4} {revisit:>5} "
          f"{hits_before:>4} {'YES' if escaping else '   ':>3}  "
          f"{'->' + str(new_pos) if moved else 'WAIT':<14}{flag}")

    pos = new_pos
    memory.visited[pos] += 1
    recent = memory.recent_positions.setdefault(UID, [])
    recent.append(pos)
    if len(recent) > 16:
        del recent[: len(recent) - 16]

print()
print(f"总逃生触发: {memory.decision_totals.get('lightning:escape_triggered', 0)}")
