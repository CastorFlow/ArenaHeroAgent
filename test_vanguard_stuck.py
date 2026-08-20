#!/usr/bin/env python3
"""模拟服务器上先锋陷入鬼打墙的场景，验证改进后能否走出。"""

from uuid import uuid4

from arena_hero_strategy import SmartTactic, TacticMemory
from test_arena_hero_tactic import core, make_turn, vanguard

# 模拟一个常见的乱石堆地形（基于 Lightning 方环坐标）
RUBBLE = set()
# 横向障碍带
for x in range(645, 655):
    RUBBLE.add((x, 600))
    RUBBLE.add((x, 602))
# 纵向障碍带
for y in range(598, 605):
    RUBBLE.add((648, y))
    RUBBLE.add((650, y))
# 散落石块
for pos in [(646, 599), (647, 601), (649, 599), (651, 601), (652, 600)]:
    RUBBLE.add(pos)

print("测试场景：两个先锋陷入乱石堆鬼打墙")
print(f"障碍数量：{len(RUBBLE)}")
print()

for scenario, visited_init in [("新区域 (visited=0)", 0), ("老区域 (visited=40)", 40)]:
    print(f"=== {scenario} ===")

    memory = TacticMemory()
    tactic = SmartTactic(memory)
    memory.known_obstacles = RUBBLE.copy()

    # 预填 visited（模拟长期巡逻的区域）
    if visited_init > 0:
        for x in range(640, 660):
            for y in range(595, 610):
                memory.visited[(x, y)] = visited_init

    # 两个先锋，都在乱石堆中央（但在轨道半径 5 之外，距离 Core ~10 格）
    v1_id = uuid4()
    v2_id = uuid4()
    v1_short = str(v1_id)[:8]
    v2_short = str(v2_id)[:8]

    v1_start = (643, 600)  # Core 左侧 7 格，在障碍带内
    v2_start = (657, 595)  # Core 右上 7+5 格，在障碍带内
    v1_goal = (620, 600)   # 目标在左侧
    v2_goal = (680, 600)   # 目标在右侧

    v1_pos, v2_pos = v1_start, v2_start
    v1_path, v2_path = [v1_pos], [v2_pos]

    for tick in range(100, 200):
        turn, _ = make_turn(
            tick=tick,
            own_core=core((650, 600)),
            units=(
                vanguard(v1_pos, v1_id),
                vanguard(v2_pos, v2_id),
            ),
            obstacle_cells=tuple(RUBBLE),
        )

        summary = tactic.choose_actions(turn)

        # 提取两个先锋的移动（决策格式：vanguard:<short_id> move <dir> to=(x, y) ...）
        import re
        for decision in summary.decisions:
            if f"vanguard:{v1_short}" in decision and " move " in decision:
                m = re.search(r"to=\((-?\d+), (-?\d+)\)", decision)
                if m:
                    new_pos = (int(m.group(1)), int(m.group(2)))
                    if new_pos != v1_pos:
                        v1_path.append(new_pos)
                        v1_pos = new_pos
            elif f"vanguard:{v2_short}" in decision and " move " in decision:
                m = re.search(r"to=\((-?\d+), (-?\d+)\)", decision)
                if m:
                    new_pos = (int(m.group(1)), int(m.group(2)))
                    if new_pos != v2_pos:
                        v2_path.append(new_pos)
                        v2_pos = new_pos

        # 检查是否走出乱石堆（距离起点 > 10）
        v1_escaped = abs(v1_pos[0] - v1_start[0]) + abs(v1_pos[1] - v1_start[1]) > 10
        v2_escaped = abs(v2_pos[0] - v2_start[0]) + abs(v2_pos[1] - v2_start[1]) > 10

        if v1_escaped and v2_escaped:
            escape_count = memory.decision_totals.get("lightning:escape_triggered", 0)
            print(f"✅ 两个先锋都走出！步数={tick-100} 逃生触发={escape_count}")
            print(f"   先锋1：{v1_start} → {v1_pos} (移动{len(v1_path)-1}格)")
            print(f"   先锋2：{v2_start} → {v2_pos} (移动{len(v2_path)-1}格)")
            break
    else:
        # 100 步未走出
        from collections import Counter
        v1_unique = len(set(v1_path))
        v2_unique = len(set(v2_path))
        v1_hot = Counter(v1_path).most_common(1)[0]
        v2_hot = Counter(v2_path).most_common(1)[0]
        escape_count = memory.decision_totals.get("lightning:escape_triggered", 0)
        print(f"❌ 仍未走出。步数=100 逃生触发={escape_count}")
        print(f"   先锋1：去重={v1_unique}/100，最热格={v1_hot[0]}×{v1_hot[1]}")
        print(f"   先锋2：去重={v2_unique}/100，最热格={v2_hot[0]}×{v2_hot[1]}")
    print()

print("期望结果：两种场景下，先锋都能在 50 步内走出乱石堆，不会长期卡住。")
