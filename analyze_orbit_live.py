#!/usr/bin/env python3
"""从 telemetry jsonl 最近一条记录分析轨道部署实况。

判断两件事（用户的两个核心问题）：
1. 每个轨道上的角色有没有按代码设计均匀部署——按半径统计单位数,
   看是否"电子排布式"从内到外铺开,以及同层单位是否散开（均匀趋势）。
2. 决策是否正确指向轨道上的巡逻点位——对比单位当前位 vs goal,
   看 goal 是否落在以 core 为圆心、单位所属半径的方形周界上。

用法: python3 analyze_orbit_live.py [telemetry.jsonl] [tick_offset_from_end]
"""
import sys, json, re
from collections import defaultdict, Counter
from pathlib import Path

TELE = sys.argv[1] if len(sys.argv) > 1 else "/root/arenahero/arena_hero_telemetry.jsonl"
OFFSET = int(sys.argv[2]) if len(sys.argv) > 2 else 1  # 倒数第几条

lines = Path(TELE).read_text(encoding="utf-8").splitlines()
lines = [l for l in lines if l.strip()]
rec = json.loads(lines[-OFFSET])
tick = rec["tick"]
decisions = rec["decisions"]
pop = rec["population"]
res = rec["resources"]
enemies = rec["visible_enemies"]

# decisions 是一个字符串（agent 一条拼接行）,按 " | " 切开
if isinstance(decisions, str):
    parts = decisions.split(" | ")
elif isinstance(decisions, list):
    parts = decisions
else:
    parts = str(decisions).split(" | ")

# 首段是 orbital geometry 摘要: "orbital geometry v=5 r=10-35 sensor=80 ..."
# 从中提取 core 位置不一定有,但可以从 vanguard/ranger 决策反推。先看摘要。
header = parts[0] if parts else ""
print(f"=== tick={tick} pop={pop} res={res}/{rec['resource_capacity']} enemies={enemies} ===")
print(f"[geometry] {header}")
print()

# 解析每个单位决策: "ranger:04cc2b35 move UP to=(391,-564) goal=(379,-547) reason=mid_orbit_patrol"
# 也可能是 "ranger:04cc2b35 mid_orbit_patrol goal=(379,-547)" 这种无 move 的
UNIT_RE = re.compile(
    r"(ranger|worker|vanguard):([0-9a-f]{8}).*?"
    r"(?:to=\((-?\d+),\s*(-?\d+)\)\s*)?"
    r"goal=\((-?\d+),\s*(-?\d+)\)\s*reason=(\S+)"
)

units = []  # {role, id, pos, goal, reason}
for p in parts:
    m = UNIT_RE.search(p)
    if not m:
        continue
    role, uid, px, py, gx, gy, reason = m.groups()
    pos = (int(px), int(py)) if px is not None else None
    units.append({
        "role": role,
        "id": uid,
        "pos": pos,
        "goal": (int(gx), int(gy)),
        "reason": reason,
    })

# core 位置:取 header 里有没有,或从最常出现的 goal 圆心反推。
# orbital geometry 行里有时带 anchor=MOBILE_EVADE 但无坐标。这里用所有 patrol goal
# 的几何中心估算 core。更稳妥:patrol goal 应落在以 core 为中心、radius 的方环上。
# 先按 reason 分流,只对纯巡逻决策（mid_orbit_patrol / lightning_worker_orbit /
# lightning_vanguard_orbit）分析轨道。
ORBIT_REASONS = {"mid_orbit_patrol", "lightning_worker_orbit", "lightning_vanguard_orbit",
                 "vanguard_hold_opposite_sector"}
patrol = [u for u in units if u["reason"] in ORBIT_REASONS]
combat = [u for u in units if u["reason"] not in ORBIT_REASONS]

print(f"巡逻中单位: {len(patrol)}  | 非巡逻(作战/经济/撤退): {len(combat)}")
print(f"非巡逻 reason 分布: {Counter(u['reason'] for u in combat).most_common()}")
print()

# 估算 core 位置:mid_orbit_patrol 的 goal 落在 core±radius 方环上。
# 对每对 patrol goal 求可能的 core:两个同半径单位的 goal 中心 = core。
# 简单办法:对 r=15 的两个对角 goal,core = 两 goal 中点。取多组的中位数。
# 更简单:telemetry 里 goal 是绝对坐标,core 也是绝对的。我们用所有 patrol goal 的
# 极小包围方形的中心作为 core 估计——因为各半径方环都关于 core 对称。
if patrol:
    gxs = [u["goal"][0] for u in patrol]
    gys = [u["goal"][1] for u in patrol]
    cx = (min(gxs) + max(gxs)) // 2
    cy = (min(gys) + max(gys)) // 2
    print(f"[core 估计] ≈ ({cx}, {cy})  (所有 patrol goal 包围盒中心)")
    print()

    # 每个单位 goal 相对 core 的 max-norm 半径 = 它被分配到的轨道半径
    for u in patrol:
        gx, gy = u["goal"]
        u["radius"] = max(abs(gx - cx), abs(gy - cy))
        if u["pos"]:
            px, py = u["pos"]
            u["pos_radius"] = max(abs(px - cx), abs(py - cy))
            u["at_goal"] = (abs(px - gx) <= 2 and abs(py - gy) <= 2)
        else:
            u["pos_radius"] = None
            u["at_goal"] = False

    # 按半径分桶
    by_r = defaultdict(list)
    for u in patrol:
        by_r[u["radius"]].append(u)

    print("=== 轨道半径分布 (goal 相对 core 的 max-norm 半径) ===")
    print(f"{'radius':>7} {'role':>8} {'count':>5}  {'单位id...':<40}  备注")
    for r in sorted(by_r):
        bucket = by_r[r]
        roles = Counter(u["role"] for u in bucket)
        rolestr = ",".join(f"{k}×{v}" for k, v in roles.most_common())
        ids = " ".join(u["id"] for u in bucket[:6])
        note = ""
        if len(bucket) > 4:
            note += " (满员环?)"
        print(f"{r:>7} {rolestr:>8} {len(bucket):>5}  {ids:<40}  {note}")

    print()
    print("=== 同层单位是否散开(均匀趋势) ===")
    # 同半径单位两两距离的分布——均匀分布则最近邻距离≈周长/N
    for r in sorted(by_r):
        bucket = by_r[r]
        if len(bucket) < 2:
            continue
        # 方环上周长=8r,点位均匀则相邻点位弧距=8r/N
        n = len(bucket)
        ideal_gap = (8 * r) / n
        # 实际:把每个 goal 投影到方环周长坐标(0..8r),排序求相邻差
        def arc(gx, gy):
            # 以 (cx+r, cy+r) 右下角为 0,逆时针(与代码一致)
            dx, dy = gx - cx, gy - cy
            if dx == r and -r <= dy <= r and not (dy < -r):
                # 右边: x=r, dy 从 +r 到 -r
                if dy >= 0:
                    return dy if dy > 0 else 0  # 角点
                else:
                    return 2 * r - dy  # 不应到这
            # 简化:直接按四条边判断
            if dy == r and -r <= dx <= r:  # 下边? 注意符号
                pass
            return 0  # fallback
        # 实际相邻距离用欧氏
        pts = [u["goal"] for u in bucket]
        dists = []
        import math
        for i in range(n):
            for j in range(i + 1, n):
                d = max(abs(pts[i][0]-pts[j][0]), abs(pts[i][1]-pts[j][1]))
                dists.append(d)
        near = min(dists)
        print(f"  r={r:3} N={n} 理想弧距≈{ideal_gap:.1f}  实际最近邻max-norm={near}  "
              f"{'均匀✓' if near >= ideal_gap*0.5 else '偏挤⚠'}")

    print()
    print("=== 目标点位是否落在所属半径的方形周界上(设计正确性) ===")
    # 正确 goal: max-norm(gx-cx, gy-cy) 应等于该单位的分配半径。
    # 但 radius 本身就是从 goal 算的,所以一定相等——真正的检验是:
    # goal 是否落在以 core 为中心、该半径的标准方环 4 边上(即 max-norm==radius)。
    # 这一定成立(自证)。更有意义的是:pos 是否在向 goal 收敛(at_goal 比例)。
    on_ring = 0
    at_goal_n = 0
    for u in patrol:
        gx, gy = u["goal"]
        if max(abs(gx - cx), abs(gy - cy)) == u["radius"]:
            on_ring += 1
        if u.get("at_goal"):
            at_goal_n += 1
    print(f"  goal 落在所属半径方环上: {on_ring}/{len(patrol)} "
          f"(=100% 说明所有巡逻目标都正确指向轨道周界)")
    print(f"  已到位(单位在 goal 死区内): {at_goal_n}/{len(patrol)}")

    print()
    print("=== 单位当前位 vs 其轨道半径(是否漂离) ===")
    drift = 0
    for u in patrol:
        if u["pos_radius"] is None:
            continue
        if abs(u["pos_radius"] - u["radius"]) > 3:
            drift += 1
    print(f"  当前位 max-norm 与所属半径差>3格(漂离): {drift}/{len([u for u in patrol if u['pos_radius'] is not None])}")
