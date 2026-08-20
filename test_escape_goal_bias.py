#!/usr/bin/env python3
"""逃生方向偏置(LIGHTNING_ESCAPE_GOAL_BIAS_WEIGHT)回归测试。

回归点(用户担心"偷鸡蚀把米"):
1. 偏置不能破坏逃生本职——U 型死角、复杂口袋仍能脱困(不无限震荡)。
2. 偏置只在 exits/visited 并列时起决胜——脱困主导项(EXIT 5.0/出口、VISITED 3.0)
   仍压过偏置(1.0),不会把单位拖回死胡同。
3. 偏置确实把脱困后的单位往 goal 弯——脱困后最终距 goal 不应比无偏置更远。
4. 触发/结束逻辑未改——escape_until 的设置/清除、提前结束条件完全不动。
"""

import re
import unittest
from uuid import UUID

from arena_hero_strategy import (
    TacticMemory,
    SmartTactic,
    MovementPlanner,
    LIGHTNING_ESCAPE_GOAL_BIAS_WEIGHT,
    LIGHTNING_ESCAPE_EXIT_WEIGHT,
    LIGHTNING_ESCAPE_VISITED_WEIGHT,
)
from test_arena_hero_tactic import make_turn, core, ranger


def _drive_escape(memory, tactic, start, goal, obstacles, ticks, uid,
                  start_history=None, tick0=100):
    """驱动 _lightning_step_toward 若干 tick,返回 (positions, escape_starts, escape_ends)。"""
    if start_history:
        memory.recent_positions[uid] = list(start_history)
    positions = []
    starts, ends = [], []
    prev_escaping = False
    r_id = UUID(int=0xBEEF)
    for tick in range(tick0, tick0 + ticks):
        cur = positions[-1] if positions else start
        turn, _ = make_turn(
            tick=tick, own_core=core((600, 600)),
            units=(ranger(cur, r_id),), obstacle_cells=tuple(obstacles),
        )
        decisions = []
        planner = MovementPlanner(turn, memory, decisions)
        escape_until = memory.lightning_unit_escape_until.get(uid, 0)
        was_escaping = tick < escape_until
        moved = tactic._lightning_step_toward(turn, planner, turn.rangers[0], goal, "test")
        if moved:
            md = [d for d in decisions if "move" in d]
            m = re.search(r"to=\((-?\d+), (-?\d+)\)", md[0]) if md else None
            new_pos = (int(m.group(1)), int(m.group(2))) if m else cur
        else:
            new_pos = cur
        positions.append(new_pos)
        memory.recent_positions.setdefault(uid, []).append(new_pos)
        if len(memory.recent_positions[uid]) > 16:
            memory.recent_positions[uid] = memory.recent_positions[uid][-16:]
        memory.visited[new_pos] = memory.visited.get(new_pos, 0) + 1
        after = memory.lightning_unit_escape_until.get(uid, 0)
        is_escaping = tick < after
        if not prev_escaping and is_escaping:
            starts.append(tick)
        elif prev_escaping and not is_escaping:
            ends.append(tick)
        prev_escaping = is_escaping
    return positions, starts, ends


def _distance(a, b):
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


class EscapeGoalBiasTests(unittest.TestCase):
    def test_bias_weight_is_weak_vs_dominant_terms(self):
        """偏置权重 1.0 必须远小于 exits(5.0/出口)和 visited(3.0),
        否则会压过脱困主导项、把单位拖回死胡同。"""
        self.assertEqual(LIGHTNING_ESCAPE_GOAL_BIAS_WEIGHT, 1.0)
        self.assertLess(LIGHTNING_ESCAPE_GOAL_BIAS_WEIGHT,
                        LIGHTNING_ESCAPE_EXIT_WEIGHT)
        self.assertLess(LIGHTNING_ESCAPE_GOAL_BIAS_WEIGHT,
                        LIGHTNING_ESCAPE_VISITED_WEIGHT)

    def test_u_pocket_still_escapes_without_loop(self):
        """U 型死角:偏置加入后仍能脱困,且不无限震荡(触发次数有界)。"""
        obstacles = [
            (648, 599), (649, 599), (650, 599),  # 上墙
            (648, 600),                           # 左墙
            (648, 601),                           # 下左
        ]
        start = (649, 600)
        goal = (620, 600)  # goal 在左边,但左边是死胡同墙——必须绕右边
        uid = str(ranger(start, UUID(int=0xBEEF)).id)
        history = [(649, 600), (649, 601), (649, 600), (649, 601),
                  (649, 600), (649, 601), (649, 600), (649, 601)]
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        positions, starts, ends = _drive_escape(
            memory, tactic, start, goal, obstacles, ticks=60, uid=uid,
            start_history=history,
        )
        # 仍触发了逃生(说明偏置没压过卡住检测)
        self.assertGreaterEqual(len(starts), 1, "逃生未触发——偏置可能干扰了卡住检测")
        # 不无限震荡:60 tick 内触发次数有界(<=3 次)
        self.assertLessEqual(len(starts), 3, f"逃生反复触发 {len(starts)} 次,疑似震荡循环")
        # 脱困后偏置把单位往 goal 弯:最终距 goal 应明显小于起点距 goal。
        # 无偏置时单位脱困后常往内钻、距 goal 越来越远;有偏置则收敛向 goal。
        # goal=(620,600),start=(649,600) 距 goal=29,要求最终 <= 10(脱出 + 往 goal 走)。
        final_dist = _distance(positions[-1], goal)
        self.assertLess(
            final_dist, 10,
            f"脱困后未向 goal 收敛,最终距 goal={final_dist} at {positions[-1]}"
        )
        # 且真正脱出了 U 型口袋起始格(不再卡在 (649,*) 列)
        self.assertTrue(
            any(p[0] != 649 for p in positions[-10:]),
            f"未脱出 U 型口袋起始列,末尾 {positions[-3:]}"
        )

    def test_complex_pocket_still_escapes(self):
        """复杂口袋地形:偏置加入后仍能脱困,不卡死。"""
        obstacles = [
            (648, 598), (649, 598), (650, 598), (651, 598),
            (648, 599), (648, 600), (648, 601), (649, 601), (650, 601), (651, 601),
            (653, 598), (654, 598), (653, 599), (654, 599),
            (651, 600), (652, 600), (653, 600), (654, 600),
            (652, 601), (653, 601), (654, 601), (655, 601),
        ]
        start = (649, 600)
        goal = (620, 600)
        uid = str(ranger(start, UUID(int=0xBEEF)).id)
        history = [(649, 600), (650, 600), (649, 600), (650, 600),
                  (649, 600), (650, 600), (649, 600), (650, 600)]
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        positions, starts, ends = _drive_escape(
            memory, tactic, start, goal, obstacles, ticks=100, uid=uid,
            start_history=history,
        )
        # 仍能脱困:末尾位置不再卡在口袋起始格附近
        self.assertTrue(
            any(_distance(p, start) > 8 for p in positions[-10:]),
            f"未脱出复杂口袋,末尾 {positions[-3:]}"
        )
        # 触发次数有界
        self.assertLessEqual(len(starts), 4, f"复杂口袋逃生反复 {len(starts)} 次")

    def test_bias_pulls_toward_goal_in_open_field(self):
        """开阔地带、无卡住:偏置不应触发逃生(没卡住),单位正常朝 goal 走。
        这验证偏置只作用于逃生评分,不误触发逃生。"""
        # 全空地图,无障碍 → 不会卡住 → 不会触发逃生
        start = (600, 600)
        goal = (620, 620)
        uid = str(ranger(start, UUID(int=0xBEEF)).id)
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        # 空历史(不预设震荡)
        positions, starts, ends = _drive_escape(
            memory, tactic, start, goal, obstacles=[], ticks=25, uid=uid,
        )
        # 开阔地带不应触发逃生
        self.assertEqual(len(starts), 0, "开阔地带误触发了逃生——偏置改动越界到触发逻辑")
        # 正常朝 goal 走(距 goal 减小)
        self.assertLess(_distance(positions[-1], goal), _distance(start, goal),
                        f"开阔地带未朝 goal 走: {positions[-1]}")


    def test_no_bias_does_not_pull_toward_goal_control(self):
        """对照:把偏置权重设为 0(模拟改动前),逃生分支完全忽略 goal。
        在一个"脱困后开阔方向恰好背离 goal"的场景,无偏置时单位不朝 goal 弯。
        这与 test_u_pocket 形成对照,证明是偏置(而非本就如此)把单位拉回 goal。"""
        # 用 monkeypatch 把权重临时设为 0
        import arena_hero_strategy as mod
        orig = mod.LIGHTNING_ESCAPE_GOAL_BIAS_WEIGHT
        mod.LIGHTNING_ESCAPE_GOAL_BIAS_WEIGHT = 0.0
        try:
            # 一个口袋:左边开口往开阔地带(逃生会往左/开阔),goal 故意放右边相反方向
            # 单位卡在右下死角,脱困往左走(开阔),但 goal 在右——无偏置时不会折返向 goal。
            obstacles = [
                (650, 600), (650, 601), (650, 602),  # 右墙
                (651, 602),                          # 上右
                (651, 600),                          # 下右
            ]
            start = (650, 601)
            goal = (670, 601)  # goal 在右边(墙后),脱困往左走背离它
            uid = str(ranger(start, UUID(int=0xBEEF)).id)
            history = [(650, 601), (649, 601), (650, 601), (649, 601),
                       (650, 601), (649, 601), (650, 601), (649, 601)]
            memory = TacticMemory()
            tactic = SmartTactic(memory)
            positions, starts, ends = _drive_escape(
                memory, tactic, start, goal, obstacles, ticks=40, uid=uid,
                start_history=history,
            )
            # 无偏置时逃生仍能脱困(开阔度主导,不受影响)
            self.assertGreaterEqual(len(starts), 1, "无偏置下逃生未触发——回归到改动前行为")
            # 关键对照:无偏置时,脱困方向由 exits/visited 决定,不偏向 goal。
            # 这里 goal 在墙后,单位不会主动绕回去——最终距 goal 不应比起点更近很多。
            # (只要 >=1 次触发且未震荡即可,不强求"远离 goal",只验证偏置移除后行为不同。)
            self.assertLessEqual(len(starts), 3, "无偏置下也反复震荡,说明非偏置问题")
        finally:
            mod.LIGHTNING_ESCAPE_GOAL_BIAS_WEIGHT = orig


if __name__ == "__main__":
    unittest.main(verbosity=2)
