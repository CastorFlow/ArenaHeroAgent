"""网页控制台新增控制：造兵预定队列的单元/集成测试。

覆盖 arena_hero_strategy.py 的 _select_spawn_with_source / _consume_build_queue：
- 队列优先于固定阶梯/默认比例
- 队列扫描跳过已封顶兵种
- 队列消费（内存 + 控制文件双消费，竞态守卫不覆盖外部改动）
- 预算不足保队列（不消费）
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from uuid import UUID

from arena_hero import UnitType

import arena_hero_strategy as strategy_module
from test_arena_hero_tactic import core, make_turn, ranger, vanguard, worker

SmartTactic = strategy_module.SmartTactic
TacticMemory = strategy_module.TacticMemory


def _units(population: int):
    """构造 population 个单位的 tuple。

    默认 spawn_ratio 现为三元 {ranger:1, vanguard:1, worker:3}，fixture 需造齐
    三类兵才能反映真实对局分布，避免某类计数为 0 时被比例趋近逻辑误选。
    5 个一组循环：ranger / vanguard / worker / ranger / worker，保证 pop≥9 时
    每类都非零。
    """
    pattern = (
        UnitType.RANGER,
        UnitType.VANGUARD,
        UnitType.WORKER,
        UnitType.RANGER,
        UnitType.WORKER,
    )
    units = []
    for index in range(1, population + 1):
        uid = UUID(int=index)
        kind = pattern[(index - 1) % len(pattern)]
        position = (index * 10, index * 10)
        if kind is UnitType.WORKER:
            units.append(worker(uid, position))
        elif kind is UnitType.VANGUARD:
            units.append(vanguard(position, uid))
        else:
            units.append(ranger(position, uid))
    return tuple(units)


def _turn(population: int, resources: int):
    return make_turn(
        tick=100,
        own_core=core((600, 600)),
        units=_units(population),
        resources=resources,
    )[0]


class BuildQueueUnitTests(unittest.TestCase):
    def _decide(self, population: int, resources: int, *, queue, ratio=None, caps=None):
        memory = TacticMemory()
        memory.build_queue = list(queue)
        if ratio is not None:
            memory.spawn_ratio = ratio
        if caps is not None:
            memory.unit_caps = caps
        memory.wartime_reserve = 0  # 测试里关掉存底干扰
        turn = _turn(population, resources)
        tactic = SmartTactic(memory)
        return tactic._select_spawn_with_source(turn, resources)

    def test_queue_overrides_ratio(self) -> None:
        # 无队列时按比例趋近；队列指定 WORKER → 队列优先于比例。
        spawn, from_queue = self._decide(3, 200, queue=["WORKER"])
        self.assertIs(spawn, UnitType.WORKER)
        self.assertTrue(from_queue)

    def test_queue_scans_past_capped_head(self) -> None:
        # 队首 WORKER 封顶（wk>=cap）→ 跳过选 RANGER。
        # pop=4 fixture: rk=2, vg=1, wk=1；worker 上限 1 → 已封顶。
        spawn, from_queue = self._decide(
            4, 200, queue=["WORKER", "RANGER"], caps={"worker": 1, "ranger": 0}
        )
        self.assertIs(spawn, UnitType.RANGER)
        self.assertTrue(from_queue)

    def test_queue_all_capped_returns_none(self) -> None:
        # pop=4 fixture: rk=2, vg=1, wk=1；worker/ranger 全封顶 → None。
        spawn, from_queue = self._decide(
            4, 200, queue=["WORKER", "RANGER"], caps={"worker": 1, "ranger": 2}
        )
        self.assertIsNone(spawn)
        self.assertFalse(from_queue)

    def test_queue_kept_when_budget_short(self) -> None:
        spawn, from_queue = self._decide(3, 0, queue=["WORKER"])
        self.assertIsNone(spawn)
        self.assertTrue(from_queue)  # 攒钱下 tick，不消费

    def test_default_ratio_is_1_to_1_to_3(self) -> None:
        # pop=10 fixture: rk=4, vg=2, wk=4，默认 1:1:3（游侠:先锋:工人）。
        # norm: wk/3≈1.33 < rk/1=4 < vg/1=2... 实际 wk≈1.33 最低 → 补工人。
        # 工人默认上限 20，wk=4<20 未封顶 → 补工人。
        spawn, from_queue = self._decide(10, 500, queue=[])
        self.assertIs(spawn, UnitType.WORKER)
        self.assertFalse(from_queue)

    def test_ratio_can_be_reconfigured_to_1_to_1(self) -> None:
        # 1:1 游侠:工人，显式停造先锋（缺省 vanguard:1 会插队）。pop=10 fixture:
        # rk=4, wk=4，1:1 下 norm 相等 → 平局按补兵优先级取 ranger。
        spawn, _ = self._decide(
            10, 500, queue=[], ratio={"ranger": 1, "worker": 1, "vanguard": 0}
        )
        self.assertIs(spawn, UnitType.RANGER)

    def test_capped_ratio_type_falls_back(self) -> None:
        # 比例首选游侠，但游侠封顶 → 按补兵优先级找下一个未封顶的非零比例兵种。
        # 这里显式用 3:1:1（游侠 norm 最低）验证封顶回退；默认优先级
        # [ranger, worker, vanguard] → 游侠封顶后回退到 worker。
        # pop=10 fixture: rk=4, vg=2, wk=4；ranger 上限 4 → 已封顶。
        spawn, _ = self._decide(
            10, 500, queue=[],
            ratio={"ranger": 3, "vanguard": 1, "worker": 1},
            caps={"ranger": 4, "worker": 0},
        )
        self.assertIs(spawn, UnitType.WORKER)

    def test_worker_cap_does_not_passively_cap_ranger(self) -> None:
        # 用户核心诉求：工人封顶只停工人，游侠不受影响。
        # 显式 3:1:1（游侠 norm 最低）。pop=10 fixture: rk=4, vg=2, wk=4；
        # 工人上限 4（已满），游侠无上限 → 仍补游侠。
        spawn, _ = self._decide(
            10, 500, queue=[],
            ratio={"ranger": 3, "vanguard": 1, "worker": 1},
            caps={"worker": 4, "ranger": 0},
        )
        self.assertIs(spawn, UnitType.RANGER)

    def test_wartime_reserve_blocks_spend_below_floor(self) -> None:
        from arena_hero import unit_cost

        population = 10
        ranger_cost = unit_cost(UnitType.RANGER, population)
        capacity = 50
        reserve = 30  # 50-30=20 >= ranger_cost(≈12) → 存底生效
        memory = TacticMemory()
        memory.wartime_reserve = reserve
        # 显式 3:1:1 让游侠 norm 最低，验证存底对游侠造兵的节流。
        memory.spawn_ratio = {"ranger": 3, "vanguard": 1, "worker": 1}
        turn = _turn(population, 0)
        tactic = SmartTactic(memory)
        # 预算 = projected - reserve 恰好够 → 造游侠
        spawn, from_queue = tactic._select_spawn_with_source(
            turn, reserve + ranger_cost
        )
        self.assertIs(spawn, UnitType.RANGER)
        self.assertFalse(from_queue)
        # 预算 = reserve + cost - 1 → 不够 → 攒钱
        spawn, _ = tactic._select_spawn_with_source(
            turn, reserve + ranger_cost - 1
        )
        self.assertIsNone(spawn)
        # 高存底(150) 超出 capacity-cost → 存底失效（产能兜底），照常造
        memory.wartime_reserve = capacity  # capacity - reserve = 0 < cost → 无视存底
        spawn, _ = tactic._select_spawn_with_source(turn, ranger_cost)
        self.assertIs(spawn, UnitType.RANGER)


class BuildQueueConsumptionTests(unittest.TestCase):
    def test_spawn_consumes_queue_in_memory_and_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            control_path = Path(directory) / ".arena_hero_control.json"
            control_path.write_text(
                json.dumps({"build_queue": ["WORKER", "RANGER"]}),
                encoding="utf-8",
            )
            memory = TacticMemory()
            memory.wartime_reserve = 0
            tactic = SmartTactic(memory, control_path=control_path)
            turn = _turn(3, 200)  # pop=3, ladder 槽位是 RANGER，但队列 WORKER 优先
            tactic.choose_actions(turn)
            # 内存队列消费掉 WORKER
            self.assertEqual(memory.build_queue, ["RANGER"])
            # 控制文件同步消费
            on_disk = json.loads(control_path.read_text(encoding="utf-8"))
            self.assertEqual(on_disk["build_queue"], ["RANGER"])

    def test_consume_preserves_other_control_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            control_path = Path(directory) / ".arena_hero_control.json"
            control_path.write_text(
                json.dumps(
                    {
                        "build_queue": ["WORKER"],
                        "core_hold": True,
                        "core_target": [100, 200],
                        "wartime_reserve": 120,
                    }
                ),
                encoding="utf-8",
            )
            memory = TacticMemory()
            memory.wartime_reserve = 0
            tactic = SmartTactic(memory, control_path=control_path)
            turn = _turn(3, 200)
            tactic.choose_actions(turn)
            on_disk = json.loads(control_path.read_text(encoding="utf-8"))
            self.assertEqual(on_disk["build_queue"], [])
            self.assertIs(on_disk["core_hold"], True)
            self.assertEqual(on_disk["core_target"], [100, 200])
            self.assertEqual(on_disk["wartime_reserve"], 120)


if __name__ == "__main__":
    unittest.main()
