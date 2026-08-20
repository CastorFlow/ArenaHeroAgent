#!/usr/bin/env python3
"""跨 tick 追踪单个单位:goal 半径是否稳定 + pos 是否向 goal 收敛。"""
import sys, json, re
from pathlib import Path
from collections import defaultdict

TELE = sys.argv[1] if len(sys.argv) > 1 else "/root/arenahero/arena_hero_telemetry.jsonl"
N = int(sys.argv[2]) if len(sys.argv) > 2 else 12  # 取倒数 N 条记录

lines = [l for l in Path(TELE).read_text(encoding="utf-8").splitlines() if l.strip()]
records = [json.loads(l) for l in lines[-N:]]

UNIT_RE = re.compile(
    r"(ranger|worker|vanguard):([0-9a-f]{8}).*?"
    r"(?:to=\((-?\d+),\s*(-?\d+)\)\s*)?"
    r"goal=\((-?\d+),\s*(-?\d+)\)\s*reason=(\S+)"
)
ORBIT = {"mid_orbit_patrol", "lightning_worker_orbit", "lightning_vanguard_orbit"}

track = defaultdict(list)  # uid -> [(tick, reason, goal_r, pos_r, dist_to_goal)]
for rec in records:
    dec = rec["decisions"]
    parts = dec.split(" | ") if isinstance(dec, str) else dec
    patrol = []
    for p in parts:
        m = UNIT_RE.search(p)
        if not m:
            continue
        role, uid, px, py, gx, gy, reason = m.groups()
        if reason in ORBIT:
            patrol.append((uid, int(gx), int(gy),
                           (int(px), int(py)) if px is not None else None, reason))
    if not patrol:
        continue
    gxs = [g[1] for g in patrol]; gys = [g[2] for g in patrol]
    cx = (min(gxs) + max(gxs)) // 2
    cy = (min(gys) + max(gys)) // 2
    for uid, gx, gy, pos, reason in patrol:
        goal_r = max(abs(gx - cx), abs(gy - cy))
        if pos is not None:
            pos_r = max(abs(pos[0] - cx), abs(pos[1] - cy))
            dist = max(abs(pos[0] - gx), abs(pos[1] - gy))
        else:
            pos_r = None; dist = None
        track[uid].append((rec["tick"], reason, goal_r, pos_r, dist))

print(f"追踪 {len(track)} 个单位,跨 {len(records)} 条记录 (tick {records[0]['tick']}→{records[-1]['tick']})\n")
print(f"{'uid':>10}  {'goal半径历史':<30}  {'pos半径历史':<35}  诊断")
print("-" * 100)
# 按第一次出现的 goal_r 排序
def first_r(uid):
    return track[uid][0][2] if track[uid] else 0
for uid in sorted(track, key=first_r):
    hist = track[uid]
    if len(hist) < 3:
        continue
    grs = [h[2] for h in hist]
    prs = [h[3] for h in hist if h[3] is not None]
    gr_stable = len(set(grs)) == 1
    gr_str = ",".join(str(r) for r in grs)
    pr_str = ",".join(str(r) for r in prs)
    # pos_r 是否在向 goal_r 收敛:看 pos_r 序列是否趋向 goal_r
    if prs:
        last_pr = prs[-1]
        goal_r = grs[-1]
        converging = abs(last_pr - goal_r) <= 4
        diag = f"goal稳定={grs[0]}" if gr_stable else f"goal跳动!{set(grs)}"
        diag += f" | 末位pos_r={last_pr}→goal_r={goal_r} {'收敛✓' if converging else '漂离⚠'}"
    else:
        diag = "无pos数据"
    print(f"{uid:>10}  {gr_str:<30}  {pr_str:<35}  {diag}")

# 统计
print("\n=== 汇总 ===")
stable_goal = sum(1 for u in track if len(set(h[2] for h in track[u])) == 1 and len(track[u]) >= 3)
jumping_goal = sum(1 for u in track if len(set(h[2] for h in track[u])) > 1 and len(track[u]) >= 3)
print(f"goal半径稳定(分配不抖动): {stable_goal}/{len([u for u in track if len(track[u])>=3])}")
print(f"goal半径跳动(分配抖动):   {jumping_goal}/{len([u for u in track if len(track[u])>=3])}")
