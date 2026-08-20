#!/usr/bin/env python3
"""测试深口袋地形（15x15 的 U 型死角）+ visited 饱和，看能否稳定逃生。"""

from uuid import UUID

from arena_hero_strategy import SmartTactic, TacticMemory
from test_arena_hero_tactic import core, make_turn, ranger

# 深 U 型口袋：15x15，入口在右上角
DEEP_POCKET = (
    [(640, y) for y in range(590, 610)]  # 左墙
    + [(x, 590) for x in range(640, 660)]  # 底墙
    + [(659, y) for y in range(590, 605)]  # 右墙（留 5 格缺口）
    + [(x, 604) for x in range(656, 660)]  # 右墙内凸
    + [(655, y) for y in range(600, 605)]  # 内部障碍
)
START = (650, 600)  # 口袋深处
GOAL = (600, 600)
EXIT = (659, 605)  # 右上角出口

print("测试地形：15x15 深 U 型口袋，入口在右上角 (659,605)")
print(f"起点：{START} (口袋深处)")
print(f"目标：{GOAL} (向左 50 格)")
print(f"出口：{EXIT} (需要先向右、再向上才能逃出)")
print()

for scenario, visited_init in [("开局", 0), ("visited 饱和", 30)]:
    memory = TacticMemory()
    tactic = SmartTactic(memory)
    memory.known_obstacles = set(DEEP_POCKET)

    # 预填 visited（模拟跑久后的场景）
    if visited_init > 0:
        for x in range(635, 665):
            for y in range(585, 615):
                memory.visited[(x, y)] = visited_init

    pos = START
    path = [pos]
    escape_count = 0

    for tick in range(100, 250):
        turn, _ = make_turn(
            tick=tick,
            own_core=core(GOAL),
            units=(ranger(pos, UUID(int=0xC0C0)),),
            obstacle_cells=tuple(DEEP_POCKET),
        )

        summary = tactic.choose_actions(turn)
        escape_count = memory.decision_totals.get("lightning:escape_triggered", 0)

        # 提取移动目标
        new_pos = pos
        for decision in summary.decisions:
            if " move " in decision and "to=" in decision:
                import re
                m = re.search(r"to=\((-?\d+), (-?\d+)\)", decision)
                if m:
                    new_pos = (int(m.group(1)), int(m.group(2)))
                    break

        if new_pos != pos:
            path.append(new_pos)
            pos = new_pos

        # 成功逃出口袋
        if pos[0] >= EXIT[0] and pos[1] >= EXIT[1]:
            print(f"✅ {scenario:12} 成功逃出！步数={tick-100:3} 路径长={len(path):3} "
                  f"逃生触发={escape_count} 最终位置={pos}")
            break
    else:
        # 150 步未逃出
        unique = len(set(path))
        progress = abs(pos[0] - START[0]) + abs(pos[1] - START[1])
        from collections import Counter
        hot = Counter(path).most_common(1)[0]
        print(f"❌ {scenario:12} 未逃出。步数=150 去重={unique:3} 净进展={progress:3} "
              f"逃生触发={escape_count} 最热格={hot[0]}×{hot[1]}")

print()
print("期望结果：两种场景都能在 50 步内逃出口袋，visited 饱和场景不应该失效。")
