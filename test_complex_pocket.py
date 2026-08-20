#!/usr/bin/env python3
"""测试复杂口袋地形的逃生循环问题"""

from uuid import UUID
from arena_hero import Position
from arena_hero_strategy import (
    TacticMemory,
    SmartTactic,
    MovementPlanner,
    LIGHTNING_ESCAPE_DURATION_TICKS,
    LIGHTNING_ESCAPE_DETECT_WINDOW,
)
from test_arena_hero_tactic import make_turn, core, ranger


def test_complex_pocket_escape():
    """模拟复杂口袋地形：多个窄通道和死角"""
    memory = TacticMemory()
    tactic = SmartTactic(memory)

    # 复杂口袋地形：类似乱石堆的多个死角
    #   ####  ##
    #   #  #  ##
    #   #R ## ##
    #   #### ###
    obstacles = [
        # 左上角区域
        (648, 598), (649, 598), (650, 598), (651, 598),
        (648, 599),
        (648, 600),
        (648, 601), (649, 601), (650, 601), (651, 601),
        # 右上角区域
        (653, 598), (654, 598),
        (653, 599), (654, 599),
        # 右下角区域
        (651, 600), (652, 600), (653, 600), (654, 600),
        (652, 601), (653, 601), (654, 601), (655, 601),
    ]

    r_unit = ranger((649, 600), UUID(int=0xBEEF))
    uid = str(r_unit.id)

    print("=== 模拟复杂口袋逃生测试 ===\n")
    print(f"游侠起始位置: {r_unit.position}")
    print(f"目标: (620, 600) (在左边，需要穿过复杂地形)")
    print(f"逃生持续: {LIGHTNING_ESCAPE_DURATION_TICKS} ticks")
    print(f"检测窗口: {LIGHTNING_ESCAPE_DETECT_WINDOW} ticks")
    print()

    # 预设震荡历史（在口袋内横跳）
    memory.recent_positions[uid] = [
        (649, 600), (650, 600), (649, 600), (650, 600),
        (649, 600), (650, 600), (649, 600), (650, 600),
    ]

    positions_log = []
    escape_events = []
    visited_history = []

    # 模拟 100 个 tick
    for tick in range(100, 200):
        current_pos = positions_log[-1] if positions_log else r_unit.position
        turn, _ = make_turn(
            tick=tick,
            own_core=core((600, 600)),
            units=(ranger(current_pos, UUID(int=0xBEEF)),),
            obstacle_cells=tuple(obstacles),
        )

        decisions = []
        planner = MovementPlanner(turn, memory, decisions)

        # 检查是否在逃生状态
        escape_until = memory.lightning_unit_escape_until.get(uid, 0)
        was_escaping = tick < escape_until

        moved = tactic._lightning_step_toward(
            turn, planner, turn.rangers[0], (620, 600), "test"
        )

        # 更新位置
        if moved:
            import re
            move_decision = [d for d in decisions if "move" in d]
            if move_decision:
                match = re.search(r'to=\((\d+), (\d+)\)', move_decision[0])
                if match:
                    new_pos = (int(match.group(1)), int(match.group(2)))
                else:
                    new_pos = current_pos
            else:
                new_pos = current_pos

            positions_log.append(new_pos)
            memory.recent_positions.setdefault(uid, []).append(new_pos)
            if len(memory.recent_positions[uid]) > 16:
                memory.recent_positions[uid] = memory.recent_positions[uid][-16:]
            # ⚠️ 关键：模拟 visited 自动累加
            memory.visited[new_pos] += 1
            visited_history.append((tick, new_pos, memory.visited[new_pos]))
        else:
            positions_log.append(current_pos)

        # 检查逃生状态变化
        escape_until_after = memory.lightning_unit_escape_until.get(uid, 0)
        is_escaping = tick < escape_until_after

        if not was_escaping and is_escaping:
            escape_events.append((tick, "START", positions_log[-1]))
            print(f"tick={tick:3d} 🚨 逃生开始 at {positions_log[-1]}")
        elif was_escaping and not is_escaping:
            escape_events.append((tick, "END", positions_log[-1]))
            print(f"tick={tick:3d} ✅ 逃生结束 at {positions_log[-1]}")

        # 打印关键信息
        if is_escaping or was_escaping or tick < 110:
            recent_8 = memory.recent_positions.get(uid, [])[-8:]
            xs = [p[0] for p in recent_8] if recent_8 else []
            ys = [p[1] for p in recent_8] if recent_8 else []
            span = max(max(xs) - min(xs), max(ys) - min(ys)) if xs and ys else 0
            revisits = recent_8.count(positions_log[-1]) if recent_8 else 0

            print(f"tick={tick:3d} pos={positions_log[-1]} "
                  f"esc={int(is_escaping)} span={span} "
                  f"revisit={revisits} "
                  f"visited={memory.visited.get(positions_log[-1], 0)}")

    print(f"\n=== 逃生事件汇总 ===")
    for tick, event, pos in escape_events:
        print(f"tick={tick:3d} {event:5s} at {pos}")

    print(f"\n=== 分析 ===")
    start_count = sum(1 for _, event, _ in escape_events if event == "START")
    print(f"逃生触发次数: {start_count}")

    if start_count > 1:
        print("\n❌ 检测到逃生循环！")
        print("\n循环原因分析:")

        # 分析每次逃生之间的间隔
        starts = [tick for tick, event, _ in escape_events if event == "START"]
        for i in range(1, len(starts)):
            gap = starts[i] - starts[i-1]
            print(f"  第{i}次逃生: 距上次 {gap} ticks")
            if gap < LIGHTNING_ESCAPE_DURATION_TICKS + 10:
                print(f"    ⚠️ 间隔太短！逃生持续 {LIGHTNING_ESCAPE_DURATION_TICKS}，"
                      f"但 {gap} ticks 后又触发")
    else:
        print("\n✅ 单次逃生成功")

    # 分析 visited 累积
    print(f"\n=== visited 累积分析 ===")
    high_visited = [(pos, count) for pos, count in memory.visited.items() if count >= 3]
    high_visited.sort(key=lambda x: x[1], reverse=True)
    print(f"visited >= 3 的位置: {len(high_visited)} 个")
    if high_visited:
        print("Top 10 最高 visited:")
        for pos, count in high_visited[:10]:
            print(f"  {pos}: {count} 次")

    # 分析位置轨迹
    unique_positions = len(set(positions_log))
    print(f"\n位置统计:")
    print(f"  - 总移动: {len(positions_log)} 步")
    print(f"  - 不同位置: {unique_positions} 个")
    print(f"  - 重复率: {(1 - unique_positions/len(positions_log))*100:.1f}%")

    # 找出重复最多的位置
    from collections import Counter
    pos_counts = Counter(positions_log)
    most_common = pos_counts.most_common(5)
    print(f"\n重复最多的位置:")
    for pos, count in most_common:
        print(f"  {pos}: {count} 次")

    return start_count > 1  # 返回是否有循环


if __name__ == "__main__":
    has_loop = test_complex_pocket_escape()
    if has_loop:
        print("\n" + "="*60)
        print("检测到逃生循环问题！")
        print("="*60)
        exit(1)
    else:
        exit(0)
