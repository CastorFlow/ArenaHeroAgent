#!/usr/bin/env python3
"""为什么防鬼打墙会随游戏进行而失效？——visited 饱和 + 大环震荡漏检。

两个假设：
A) visited 惩罚被 min(10.0, n*0.5) 封顶。开局 visited 是 0-6，惩罚有区分度；
   跑久了轨道附近每格都被访问 20+ 次，全部顶到 10.0 → 惩罚变成常数 →
   完全失去区分度 → 退化回"纯距离+惯性"打分 → 局部最优陷阱复活。
B) 卡住检测要求 span<=2（两格 ping-pong）。绕乱石堆的鬼打墙常是 4-8 格的
   大环，span 4-6 → 永远检测不到 → 逃生根本不触发。
"""

import re
from collections import Counter
from uuid import UUID

from arena_hero_strategy import (
    LIGHTNING_ESCAPE_DETECT_SPAN,
    LIGHTNING_ESCAPE_DETECT_WINDOW,
    MovementPlanner,
    SmartTactic,
    TacticMemory,
)
from test_arena_hero_tactic import core, make_turn, ranger


def simulate(obstacles, start, goal, ticks=120, presaturate=0, label=""):
    """跑 N tick，返回 (轨迹, 逃生触发次数, 是否到达目标)。"""
    memory = TacticMemory()
    tactic = SmartTactic(memory)
    uid = str(UUID(int=0xB0B0))
    memory.known_obstacles = set(obstacles)

    if presaturate:
        # 模拟"跑了几小时"后的 visited 状态：目标周边全部访问过很多次。
        for x in range(start[0] - 25, start[0] + 15):
            for y in range(start[1] - 15, start[1] + 15):
                memory.visited[(x, y)] = presaturate

    pos = start
    trail = [pos]
    escapes = 0
    prev_escaping = False

    for tick in range(100, 100 + ticks):
        turn, _ = make_turn(
            tick=tick,
            own_core=core((600, 600)),
            units=(ranger(pos, UUID(int=0xB0B0)),),
            obstacle_cells=tuple(obstacles),
        )
        decisions: list[str] = []
        planner = MovementPlanner(turn, memory, decisions)

        moved = tactic._lightning_step_toward(
            turn, planner, turn.rangers[0], goal, "sim"
        )
        escaping = tick < memory.lightning_unit_escape_until.get(uid, 0)
        if escaping and not prev_escaping:
            escapes += 1
        prev_escaping = escaping

        if moved:
            line = next((d for d in decisions if " move " in d), "")
            match = re.search(r"to=\((-?\d+), (-?\d+)\)", line)
            if match:
                pos = (int(match.group(1)), int(match.group(2)))
        trail.append(pos)

        # 复刻真实主循环每 tick 的副作用（关键：visited 会自动累加）
        memory.visited[pos] += 1
        recent = memory.recent_positions.setdefault(uid, [])
        recent.append(pos)
        if len(recent) > 16:
            del recent[: len(recent) - 16]
        memory.unit_positions[uid] = pos
        if pos == goal:
            break

    reached = pos == goal
    return trail, escapes, reached


def report(name, trail, escapes, reached, goal):
    uniq = len(set(trail))
    final_gap = abs(trail[-1][0] - goal[0]) + abs(trail[-1][1] - goal[1])
    start_gap = abs(trail[0][0] - goal[0]) + abs(trail[0][1] - goal[1])
    progress = start_gap - final_gap
    loop = Counter(trail).most_common(1)[0]
    status = "✅ 脱困" if progress > 15 else "❌ 鬼打墙"
    print(
        f"{status} {name:22s} 步数={len(trail):3d} 去重={uniq:3d} "
        f"净进展={progress:4d} 逃生触发={escapes} 最热格={loop[0]}×{loop[1]}"
    )
    return progress > 15


# 一个典型的乱石堆口袋：开口在右，左侧是穿不过去的石墙（目标在左）
POCKET = [
    (640, y) for y in range(592, 609)
] + [
    (x, 592) for x in range(640, 656)
] + [
    (x, 608) for x in range(640, 656)
] + [
    # 内部散石，制造 4-8 格的大环绕行（不是简单的两格 ping-pong）
    (646, 598), (647, 598), (648, 598),
    (646, 602), (647, 602), (648, 602),
    (650, 600), (651, 600),
]

START = (652, 600)
GOAL = (600, 600)

print("=" * 78)
print("假设 A：visited 饱和后惩罚项失去区分度（开局有效 → 跑久了失效）")
print("=" * 78)
ok_fresh = report("开局 (visited=0)", *simulate(POCKET, START, GOAL), goal=GOAL)
ok_sat = report(
    "跑久后 (visited=25)",
    *simulate(POCKET, START, GOAL, presaturate=25),
    goal=GOAL,
)

print()
print("visited 惩罚 = min(10.0, n*0.5)，n>=20 即全部顶到 10.0")
print("  开局   n=0-6  → 惩罚 0.0-3.0，与距离项(±1)同量级，有区分度")
print("  跑久后 n>=20  → 惩罚恒为 10.0，四邻全一样 → 等于没有这一项")

print()
print("=" * 78)
print(f"假设 B：卡住检测只认 span<={LIGHTNING_ESCAPE_DETECT_SPAN} 的小 ping-pong")
print("=" * 78)
for span in (2, 4, 6):
    cycle = [(650 + (i % (span + 1)), 600) for i in range(LIGHTNING_ESCAPE_DETECT_WINDOW)]
    xs = [p[0] for p in cycle]
    detected = (max(xs) - min(xs)) <= LIGHTNING_ESCAPE_DETECT_SPAN
    print(
        f"  {span+1} 格环形震荡 (span={max(xs)-min(xs)}) → "
        f"{'✅ 能检出' if detected else '❌ 漏检，逃生永不触发'}"
    )

print()
print("=" * 78)
print("结论")
print("=" * 78)
if ok_fresh and not ok_sat:
    print("✅ 复现成功：同一地形，开局能走出去，visited 饱和后走不出去。")
    print("   → 这就是'当时修好了、跑一阵又卡住'的根因。")
else:
    print(f"开局脱困={ok_fresh} 饱和脱困={ok_sat}（需换地形加强复现）")
