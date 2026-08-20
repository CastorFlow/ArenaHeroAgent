#!/usr/bin/env python3
"""点位环巡逻系统测试：覆盖对角落位、逐级细分、反扎堆跳过、到达推进。

替代旧的"轨道均匀分布软斥力"——同半径单位不再共享 4 个角，而是按 bit-reversal
序认领互不相同的角/中点作 anchor，沿环同向逐点位扫过去。
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

from arena_hero import UnitType
from arena_hero_strategy import SmartTactic, TacticMemory, _distance
from test_arena_hero_tactic import (
    VANGUARD_ID,
    VANGUARD_TWO_ID,
    core,
    make_turn,
    ranger,
    vanguard,
)


class RingWaypointGridTests(unittest.TestCase):
    """纯函数：方形周界点位生成 + bit-reversal 对角序。"""

    def test_ring_waypoints_four_corners(self) -> None:
        # 4 个点位 = 四角，index 0 在右下角，逆时针绕行（与原 corners 一致）。
        pts = SmartTactic._lightning_ring_waypoints((600, 600), 10, 4)
        self.assertEqual(
            pts,
            ((610, 610), (610, 590), (590, 590), (590, 610)),
        )
        # 0 与 count/2 互为对角。
        self.assertEqual(_distance(pts[0], pts[2]), 4 * 10)

    def test_ring_waypoints_eight_corners_and_midpoints(self) -> None:
        # 8 个点位 = 四角 + 四边中点，oddeven 交错。
        pts = SmartTactic._lightning_ring_waypoints((600, 600), 10, 8)
        corners = (pts[0], pts[2], pts[4], pts[6])
        self.assertEqual(corners, ((610, 610), (610, 590), (590, 590), (590, 610)))
        self.assertEqual(pts[1], (610, 600))  # 右
        self.assertEqual(pts[3], (600, 590))  # 上
        self.assertEqual(pts[5], (590, 600))  # 左
        self.assertEqual(pts[7], (600, 610))  # 下
        # 对径对：(0,4),(2,6) 是对角两角(曼哈顿 4r)；(1,5),(3,7) 是对边中点(曼哈顿 2r)。
        for a, b in ((0, 4), (2, 6)):
            self.assertEqual(_distance(pts[a], pts[b]), 4 * 10)
        for a, b in ((1, 5), (3, 7)):
            self.assertEqual(_distance(pts[a], pts[b]), 2 * 10)

    def test_bit_reverse_dyadic_sequence(self) -> None:
        # M=8 的 bit-reversal 序 = 用户要的"对角优先、逐级细分"：
        # 0,4(c0/对角),2,6,1,5,3,7 —— 前 4 个是四角，5+ 补边中点且成对对角。
        got = [SmartTactic._bit_reverse(k, 3) for k in range(8)]
        self.assertEqual(got, [0, 4, 2, 6, 1, 5, 3, 7])

    def test_bit_reverse_is_bijection_distinct_anchors(self) -> None:
        # 同半径 N 个单位认领 M 个点位，anchor 互不相同（bit_reverse 是双射）→ 无共享角。
        for bits in (1, 2, 3, 4):
            M = 1 << bits
            anchors = {SmartTactic._bit_reverse(g, bits) for g in range(M)}
            self.assertEqual(len(anchors), M)
            self.assertEqual(anchors, set(range(M)))


class OrbitWaypointTests(unittest.TestCase):
    """点位环在真实 Turn 上的行为。"""

    def test_single_vanguard_starts_at_corner_and_advances(self) -> None:
        # 单先锋：anchor=BR 角；到达死区后推进到下一角（到下一个点位去打开）。
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        v = vanguard((605, 590), VANGUARD_ID)
        turn, _ = make_turn(own_core=core((600, 600)), units=(v,))
        uid = str(v.id)
        memory.lightning_orbit_phase[uid] = 0
        t1 = tactic._lightning_orbit_waypoint(turn, v, UnitType.VANGUARD)
        self.assertEqual(t1, (605, 605))  # 近轨 r=5 的 BR 角
        self.assertEqual(memory.lightning_orbit_phase[uid], 0)

        # 单位站到目标角上 → 到点推进到下一角。
        turn2, _ = make_turn(
            own_core=core((600, 600)), units=(vanguard((605, 605), VANGUARD_ID),)
        )
        memory.lightning_orbit_phase[uid] = 0
        t2 = tactic._lightning_orbit_waypoint(turn2, turn2.vanguards[0], UnitType.VANGUARD)
        self.assertEqual(t2, (605, 595))  # 右上角
        self.assertEqual(memory.lightning_orbit_phase[uid], 1)

    def test_two_vanguards_land_on_diagonal_corners(self) -> None:
        # 用户"这次意外创建了两个先锋"：同近轨 2 个 → 对角（曼哈顿 = 4*半径）。
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        v1 = vanguard((605, 590), VANGUARD_ID)
        v2 = vanguard((605, 610), VANGUARD_TWO_ID)
        turn, _ = make_turn(own_core=core((600, 600)), units=(v1, v2))
        t1 = tactic._lightning_orbit_waypoint(turn, v1, UnitType.VANGUARD)
        t2 = tactic._lightning_orbit_waypoint(turn, v2, UnitType.VANGUARD)
        self.assertIsNotNone(t1)
        self.assertIsNotNone(t2)
        self.assertNotEqual(t1, t2)
        self.assertEqual(_distance(t1, t2), 4 * 5, "两先锋应在对角两个角")

    def test_two_rangers_on_shared_ring_are_diagonal(self) -> None:
        # 6 游侠(0 工人) → shell 把前 2 个放半径 10(群 0,1)，互为对角。
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        r1 = ranger((640, 600), UUID(int=0xE000))
        r2 = ranger((640, 601), UUID(int=0xE001))
        fill = [ranger((640, 602 + i), UUID(int=0xE002 + i)) for i in range(4)]
        turn, _ = make_turn(own_core=core((600, 600)), units=(r1, r2, *fill))
        t1 = tactic._lightning_orbit_waypoint(turn, r1, UnitType.RANGER)
        t2 = tactic._lightning_orbit_waypoint(turn, r2, UnitType.RANGER)
        self.assertIsNotNone(t1)
        self.assertIsNotNone(t2)
        self.assertEqual(_distance(t1, t2), 4 * 10, "同环两游侠应对角分布")

    def test_ally_proximity_skips_occupied_waypoint(self) -> None:
        # 反扎堆核心：目标角被同环友军占着 → 跳过到下一角（干净超车而非粘住）。
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        r1 = ranger((640, 600), UUID(int=0xE100))  # radius10 group0,anchor BR(610,610)
        r2 = ranger((611, 610), UUID(int=0xE101))  # radius10 group1,占在 BR 附近
        fill = [ranger((640, 602 + i), UUID(int=0xE102 + i)) for i in range(4)]
        turn, _ = make_turn(own_core=core((600, 600)), units=(r1, r2, *fill))
        t1 = tactic._lightning_orbit_waypoint(turn, r1, UnitType.RANGER)
        # r1 的 BR 角被 r2 占 → 跳到右上角(610,590)。
        self.assertEqual(t1, (610, 590))

    def test_ally_proximity_does_not_skip_own_ring_far_waypoint(self) -> None:
        # 同环友军在远处（>3 格）→ 不误跳，目标保持。
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        r1 = ranger((640, 600), UUID(int=0xE200))  # radius10 group0,anchor BR(610,610)
        r2 = ranger((585, 610), UUID(int=0xE201))  # radius10 group1,在 TL 角附近(对角,够远)
        fill = [ranger((640, 602 + i), UUID(int=0xE202 + i)) for i in range(4)]
        turn, _ = make_turn(own_core=core((600, 600)), units=(r1, r2, *fill))
        t1 = tactic._lightning_orbit_waypoint(turn, r1, UnitType.RANGER)
        self.assertEqual(t1, (610, 610))  # BR 角，未被跳过

    def test_obstructed_waypoint_is_skipped(self) -> None:
        # 乱石堆埋目标角（尚远先知）→ 提前推下一角绕行。
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        v = vanguard((600, 590), VANGUARD_ID)
        turn, _ = make_turn(own_core=core((600, 600)), units=(v,))
        uid = str(v.id)
        memory.lightning_orbit_phase[uid] = 0
        clean = tactic._lightning_orbit_waypoint(turn, v, UnitType.VANGUARD)
        self.assertEqual(clean, (605, 605))  # 近轨 r=5 的 BR 角
        memory.lightning_orbit_phase[uid] = 0
        memory.known_obstacles = {
            (clean[0] + dx, clean[1] + dy)
            for dx in range(-2, 3)
            for dy in range(-2, 3)
        } - {clean}
        blocked = tactic._lightning_orbit_waypoint(turn, v, UnitType.VANGUARD)
        self.assertNotEqual(blocked, clean)
        self.assertEqual(memory.lightning_orbit_phase[uid], 1)

    def test_waypoint_stable_across_calls(self) -> None:
        # 同一状态连续两次调用目标一致（不抖动）。
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        r1 = ranger((640, 600), UUID(int=0xCF01))
        r2 = ranger((630, 600), UUID(int=0xCF02))
        turn, _ = make_turn(own_core=core((600, 600)), units=(r1, r2))
        a = tactic._lightning_orbit_waypoint(turn, r1, UnitType.RANGER)
        b = tactic._lightning_orbit_waypoint(turn, r1, UnitType.RANGER)
        self.assertEqual(a, b)

    def test_stale_phase_from_old_system_is_reset_to_anchor(self) -> None:
        # 旧系统(4 角 phase 0..3)迁移：存量单位 phase 无对应 anchor → 强制重置回
        # 自己的 anchor，避免起点撞车。4 游侠同半径 r=10，旧 phase 全设为 0（旧角序号，
        # 旧系统会让他们共享同一角）→ 新系统应各自落到 4 个不同角。
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        rs = [ranger((640, 600 + i), UUID(int=0xD000 + i)) for i in range(4)]
        turn, _ = make_turn(own_core=core((600, 600)), units=tuple(rs))
        # 手动把 4 个游侠放同一半径 r=10，group 0..3，并塞入旧的 phase=0。
        lanes = {
            str(r.id): (10, i) for i, r in enumerate(rs)
        }
        memory.lightning_orbit_lanes[UnitType.RANGER.value] = lanes
        for r in rs:
            memory.lightning_orbit_phase[str(r.id)] = 0  # 旧角序号
        targets = {
            tactic._lightning_orbit_waypoint(turn, r, UnitType.RANGER)
            for r in rs
        }
        # anchor 不匹配 → 全部重置回各自 anchor，4 个角互不相同。
        self.assertEqual(len(targets), 4, "旧 phase 迁移后应各自锚定到不同角")
        # 且 anchor 已记录。
        for r in rs:
            self.assertIn(str(r.id), memory.lightning_orbit_anchor)

    def test_save_load_anchor_roundtrip(self) -> None:
        # strong anchor 随 memory 落盘/恢复。
        memory = TacticMemory()
        memory.lightning_orbit_anchor["u-1"] = 3
        memory.lightning_orbit_phase["u-1"] = 2
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "memory.json"
            memory.save(path)
            restored = TacticMemory.load(path)
        self.assertEqual(restored.lightning_orbit_anchor, {"u-1": 3})
        self.assertEqual(restored.lightning_orbit_phase, {"u-1": 2})

    def test_claim_resolution_keeps_same_ring_targets_distinct(self) -> None:
        # 反扎堆核心：同环单位即使 offset 漂移，目标点位也必须互不相同（占用解析）。
        # 构造近轨 r=5 的 6 个先锋（N=6→M=8），offset 故意漂移，验证每个单位最终
        # 认领互不相同的点位（对应 vps168 实测 6 游侠同层 3 单位同 target 的工况）。
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        vs = [vanguard((640, 600 + i), UUID(int=0xDD00 + i)) for i in range(6)]
        turn, _ = make_turn(own_core=core((600, 600)), units=tuple(vs))
        # 近轨分配：全部 r=5，group 0..5（uuid 序）。N=6 → M=8。
        offsets = {0: 2, 1: 0, 2: 1, 3: 3, 4: 1, 5: 2}  # 漂移的 offset
        for i, v in enumerate(vs):
            uid = str(v.id)
            anchor = tactic._bit_reverse(i, 3) % 8  # group = i（uuid 升序）
            memory.lightning_orbit_anchor[uid] = anchor
            memory.lightning_orbit_phase[uid] = offsets[i]
        targets = {
            tactic._lightning_orbit_waypoint(turn, v, UnitType.VANGUARD)
            for v in vs
        }
        self.assertEqual(len(targets), 6, "同环 6 单位目标点位必须互不相同")
        idxs = {
            (memory.lightning_orbit_anchor[str(v.id)] + memory.lightning_orbit_phase[str(v.id)]) % 8
            for v in vs
        }
        self.assertEqual(len(idxs), 6, "同环 6 单位点位序号必须互不相同")

    def test_full_ring_does_not_deadlock_parked_units(self) -> None:
        # 满员环(N=M=8)死锁回归：8 个先锋各停在各自点位(都到位)，旧实现会因"下一个
        # 点位被停驻单位占用"而全部死锁原地不动；新实现"已停驻不占用"让单位能轮转。
        memory = TacticMemory()
        tactic = SmartTactic(memory)
        M = 8
        wps = tactic._lightning_ring_waypoints((600, 600), 5, M)
        # 每个先锋站在自己 anchor 点位上(offset=0，已停驻)。
        vs = []
        for i in range(8):
            anchor = tactic._bit_reverse(i, 3) % M
            v = vanguard(wps[anchor], UUID(int=0xDE00 + i))
            vs.append(v)
            memory.lightning_orbit_anchor[str(v.id)] = anchor
            memory.lightning_orbit_phase[str(v.id)] = 0
        turn, _ = make_turn(own_core=core((600, 600)), units=tuple(vs))
        # 任取一个单位：满员环下它仍应推进到下一个点位(死锁解除)，而非返回自己位置。
        v0 = vs[0]
        target = tactic._lightning_orbit_waypoint(turn, v0, UnitType.VANGUARD)
        self.assertIsNotNone(target)
        self.assertNotEqual(target, v0.position, "满员环停驻单位应能推进到下一点位，不能死锁驻停")


if __name__ == "__main__":
    unittest.main()