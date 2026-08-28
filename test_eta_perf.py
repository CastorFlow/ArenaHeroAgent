#!/usr/bin/env python3
"""eta() 三道寻路防线的回归测试。

背景:choose_actions 的 _lightning_plan_triage/_funnel 对 vacancy×ranger、
block×worker 各调一次 planner.eta(),每次跑完整 A*;敌方拉开战线后单次寻路
随距离平方级变贵,历史上曾把单 turn 顶到 33s → 全 409 TICK_MISMATCH。

三道防线:
  ① 墙钟软预算:超过 ETA_DEADLINE_BUDGET_S 后退化为曼哈顿,结构上保证不超窗;
  ② turn 级缓存去重:同 (unit.position, goal) 只算一次;
  ③ 估算专用较小 max_expansions:单次 A* 成本封顶。
"""

import time
import unittest
from uuid import UUID

import arena_hero_strategy
from arena_hero_strategy import (
    ETA_DEADLINE_BUDGET_S,
    ETA_ESTIMATE_MAX_EXPANSIONS,
    MovementPlanner,
    TacticMemory,
    _distance,
)
from test_arena_hero_tactic import core, make_turn, ranger


def _planner(units, obstacle_cells=()):
    memory = TacticMemory()
    turn, _ = make_turn(
        tick=100,
        own_core=core((600, 600)),
        units=units,
        obstacle_cells=obstacle_cells,
    )
    return turn, MovementPlanner(turn, memory, [])


class _FindPathSpy:
    """临时替换模块级 _find_path,记录调用次数与最后一次的 max_expansions。"""

    def __init__(self):
        self.calls = 0
        self.last_max_expansions = None
        self._real = arena_hero_strategy._find_path

    def __enter__(self):
        def spy(*args, **kwargs):
            self.calls += 1
            self.last_max_expansions = kwargs.get("max_expansions")
            return self._real(*args, **kwargs)

        arena_hero_strategy._find_path = spy
        return self

    def __exit__(self, *exc):
        arena_hero_strategy._find_path = self._real


class EtaCacheTests(unittest.TestCase):
    def test_repeated_call_hits_cache(self) -> None:
        # 同 (unit.position, goal) 第二次调用不应再跑 A*。
        turn, planner = _planner((ranger((640, 600), UUID(int=1)),))
        unit = turn.rangers[0]
        goal = (660, 600)
        with _FindPathSpy() as spy:
            first = planner.eta(unit, goal)
            second = planner.eta(unit, goal)
        self.assertEqual(first, second)
        self.assertEqual(spy.calls, 1, "第二次相同查询应命中缓存,不重复寻路")
        self.assertIn(((640, 600), goal), planner._eta_cache)

    def test_distinct_goals_not_collapsed(self) -> None:
        turn, planner = _planner((ranger((640, 600), UUID(int=1)),))
        unit = turn.rangers[0]
        with _FindPathSpy() as spy:
            planner.eta(unit, (660, 600))
            planner.eta(unit, (600, 640))
        self.assertEqual(spy.calls, 2, "不同 goal 是不同 key,不应互相命中")
        self.assertEqual(len(planner._eta_cache), 2)

    def test_same_position_same_goal_shares_entry(self) -> None:
        # _blocked 的唯一每单位差异是"不挡自己所在格";同格同 goal → blocked 集
        # 相同 → 结果相同,故共享缓存项是正确的。
        turn, planner = _planner(
            (ranger((640, 600), UUID(int=1)), ranger((640, 600), UUID(int=2))),
        )
        u1, u2 = turn.rangers[0], turn.rangers[1]
        self.assertEqual(u1.position, u2.position)
        with _FindPathSpy() as spy:
            a = planner.eta(u1, (660, 600))
            b = planner.eta(u2, (660, 600))
        self.assertEqual(a, b)
        self.assertEqual(spy.calls, 1)


class EtaDeadlineTests(unittest.TestCase):
    def test_over_budget_falls_back_to_manhattan(self) -> None:
        turn, planner = _planner((ranger((640, 600), UUID(int=1)),))
        unit = turn.rangers[0]
        goal = (660, 612)
        # 把起点时间推到远超预算之前 → eta 应立即返回曼哈顿,不跑 A*。
        planner._turn_start = time.perf_counter() - (ETA_DEADLINE_BUDGET_S + 5.0)
        with _FindPathSpy() as spy:
            result = planner.eta(unit, goal)
        self.assertEqual(spy.calls, 0, "超预算后不应再寻路")
        self.assertEqual(result, _distance((640, 600), goal))

    def test_within_budget_still_pathfinds(self) -> None:
        turn, planner = _planner((ranger((640, 600), UUID(int=1)),))
        unit = turn.rangers[0]
        with _FindPathSpy() as spy:
            planner.eta(unit, (660, 600))
        self.assertEqual(spy.calls, 1, "预算内应正常寻路")


class EtaBoundsAndCorrectnessTests(unittest.TestCase):
    def test_uses_estimate_max_expansions(self) -> None:
        turn, planner = _planner((ranger((640, 600), UUID(int=1)),))
        unit = turn.rangers[0]
        with _FindPathSpy() as spy:
            planner.eta(unit, (660, 600))
        self.assertEqual(spy.last_max_expansions, ETA_ESTIMATE_MAX_EXPANSIONS)

    def test_open_field_eta_is_axis_distance(self) -> None:
        # 开阔地带直线可达:eta 应等于步数(=曼哈顿)。
        turn, planner = _planner((ranger((640, 600), UUID(int=1)),))
        unit = turn.rangers[0]
        self.assertEqual(planner.eta(unit, (645, 600)), 5)

    def test_same_cell_is_zero(self) -> None:
        turn, planner = _planner((ranger((640, 600), UUID(int=1)),))
        unit = turn.rangers[0]
        with _FindPathSpy() as spy:
            self.assertEqual(planner.eta(unit, (640, 600)), 0)
        self.assertEqual(spy.calls, 0, "起点即目标,零成本短路")

    def test_avoid_bypasses_cache(self) -> None:
        # avoid 非空时不进缓存(key 不含 avoid,缓存会污染)。
        turn, planner = _planner((ranger((640, 600), UUID(int=1)),))
        unit = turn.rangers[0]
        with _FindPathSpy() as spy:
            planner.eta(unit, (660, 600), avoid=[(650, 600)])
            planner.eta(unit, (660, 600), avoid=[(650, 600)])
        self.assertEqual(spy.calls, 2, "带 avoid 的查询不缓存,每次都算")
        self.assertEqual(planner._eta_cache, {})


if __name__ == "__main__":
    unittest.main()
