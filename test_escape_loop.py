#!/usr/bin/env python3
"""测试逃生机制的循环问题"""

from uuid import UUID
from arena_hero_strategy import (
    TacticMemory,
    SmartTactic,
    MovementPlanner,
    LIGHTNING_ESCAPE_DURATION_TICKS,
    LIGHTNING_ESCAPE_DETECT_WINDOW,
)
from test_arena_hero_tactic import make_turn, core, ranger


def test_escape_loop_in_u_pocket():
    """模拟 U 型死角的逃生循环问题"""
    memory = TacticMemory()
    tactic = SmartTactic(memory)

    # U 型死角：三面墙，只有右边开口
    #   ###
    #   # #
    #   # R
    obstacles = [
        (648, 599), (649, 599), (650, 599),  # 上墙
        (648, 600),                           # 左墙
        (648, 601),                           # 下左
    ]

    r_unit = ranger((649, 600), UUID(int=0xDEAD))
    uid = str(r_unit.id)

    print("=== 模拟 U 型死角逃生测试 ===\n")
    print(f"障碍物: {obstacles}")
    print(f"游侠起始位置: {r_unit.position}")
    print("目标: (620, 600) (在左边，但需要绕过 U 型)")
    print("\n逃生参数:")
    print(f"  - 检测窗口: {LIGHTNING_ESCAPE_DETECT_WINDOW} ticks")
    print(f"  - 逃生持续: {LIGHTNING_ESCAPE_DURATION_TICKS} ticks")
    print()

    # 预设震荡历史（在 U 型内横跳）
    memory.recent_positions[uid] = [
        (649, 600), (649, 601), (649, 600), (649, 601),
        (649, 600), (649, 601), (649, 600), (649, 601),
    ]

    positions_log = []
    escape_events = []

    # 模拟 40 个 tick
    for tick in range(100, 140):
        turn, _ = make_turn(
            tick=tick,
            own_core=core((600, 600)),
            units=(ranger(positions_log[-1] if positions_log else r_unit.position, UUID(int=0xDEAD)),),
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

        # 更新位置（模拟移动成功）
        if moved:
            # 从决策中提取目标位置
            move_decision = [d for d in decisions if "move" in d]
            if move_decision:
                # 解析 "to=(x, y)" 格式
                import re
                match = re.search(r'to=\((\d+), (\d+)\)', move_decision[0])
                if match:
                    new_pos = (int(match.group(1)), int(match.group(2)))
                else:
                    new_pos = turn.rangers[0].position
            else:
                new_pos = turn.rangers[0].position

            positions_log.append(new_pos)
            memory.recent_positions.setdefault(uid, []).append(new_pos)
            if len(memory.recent_positions[uid]) > 16:
                memory.recent_positions[uid] = memory.recent_positions[uid][-16:]
            # 模拟 visited 自动累加
            memory.visited[new_pos] += 1
        else:
            positions_log.append(turn.rangers[0].position)

        # 检查逃生状态变化
        escape_until_after = memory.lightning_unit_escape_until.get(uid, 0)
        is_escaping = tick < escape_until_after

        if not was_escaping and is_escaping:
            escape_events.append((tick, "START", positions_log[-1]))
        elif was_escaping and not is_escaping:
            escape_events.append((tick, "END", positions_log[-1]))

        # 打印关键信息
        if tick < 105 or is_escaping or was_escaping:
            recent_8 = memory.recent_positions.get(uid, [])[-8:]
            xs = [p[0] for p in recent_8] if recent_8 else []
            ys = [p[1] for p in recent_8] if recent_8 else []
            span = max(max(xs) - min(xs), max(ys) - min(ys)) if xs and ys else 0

            print(f"tick={tick:3d} pos={positions_log[-1]} "
                  f"escaping={is_escaping} span={span} "
                  f"visited={memory.visited.get(positions_log[-1], 0)}")

    print("\n=== 逃生事件汇总 ===")
    for tick, event, pos in escape_events:
        print(f"tick={tick:3d} {event:5s} at {pos}")

    print("\n=== 分析 ===")
    print(f"总逃生事件: {len(escape_events)}")

    # 统计循环次数
    start_count = sum(1 for _, event, _ in escape_events if event == "START")
    print(f"逃生触发次数: {start_count}")

    if start_count > 1:
        print("\n❌ 检测到逃生循环！单位在逃生-重新卡住之间反复。")
        print("\n可能原因:")
        print("1. 逃生持续时间不足以完全脱出死角")
        print("2. 逃生路径的 visited 累积，导致重新进入时再次卡住")
        print("3. 逃生结束判定（距离 > 8）与实际移动距离不匹配")
    else:
        print("\n✅ 单次逃生成功")

    # 分析位置轨迹
    unique_positions = len(set(positions_log))
    print("\n位置统计:")
    print(f"  - 总移动: {len(positions_log)} 步")
    print(f"  - 不同位置: {unique_positions} 个")
    print(f"  - 重复率: {(1 - unique_positions/len(positions_log))*100:.1f}%")


if __name__ == "__main__":
    test_escape_loop_in_u_pocket()
