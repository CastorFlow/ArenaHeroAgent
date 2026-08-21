#!/usr/bin/env python3
"""从VPS日志中提取最新的轨道分配快照并分析"""

import re
import subprocess
from collections import defaultdict

def extract_positions(log_text: str):
    """从日志中提取单位位置"""
    core = (539, -649)

    rangers = []
    vanguards = []
    workers = []

    # 提取游侠
    for match in re.finditer(r'ranger:(\w+) \w+ \w+ to=\((-?\d+), (-?\d+)\).*?lane=(\d+)', log_text):
        unit_id, x, y, lane = match.groups()
        rangers.append({
            'id': unit_id[:8],
            'x': int(x),
            'y': int(y),
            'lane': int(lane),
            'radius': max(abs(int(x) - core[0]), abs(int(y) - core[1]))
        })

    # 提取先锋
    for match in re.finditer(r'vanguard:(\w+) \w+ \w+ to=\((-?\d+), (-?\d+)\).*?lightning_vanguard_orbit', log_text):
        unit_id, x, y = match.groups()
        vanguards.append({
            'id': unit_id[:8],
            'x': int(x),
            'y': int(y),
            'radius': max(abs(int(x) - core[0]), abs(int(y) - core[1]))
        })

    # 提取工人（仅在轨道上的）
    for match in re.finditer(r'worker:(\w+) \w+ \w+ to=\((-?\d+), (-?\d+)\).*?lightning_worker_orbit', log_text):
        unit_id, x, y = match.groups()
        workers.append({
            'id': unit_id[:8],
            'x': int(x),
            'y': int(y),
            'radius': max(abs(int(x) - core[0]), abs(int(y) - core[1]))
        })

    return rangers, vanguards, workers


def analyze_orbits(units, unit_type, ideal_spacing):
    """分析轨道分布"""
    if not units:
        return None

    by_radius = defaultdict(list)
    for u in units:
        by_radius[u['radius']].append(u)

    print(f"\n{'='*70}")
    print(f"【{unit_type}】 总计 {len(units)} 个")
    print(f"{'='*70}")
    print(f"{'半径':<8} {'单位数':<8} {'周长':<10} {'间距':<12} {'评估':<10} {'详细'}")
    print('-' * 70)

    total_coverage = 0
    for radius in sorted(by_radius.keys()):
        units_here = by_radius[radius]
        count = len(units_here)
        circumference = 8 * radius
        spacing = circumference / count if count > 0 else 0

        # 评估
        if spacing <= ideal_spacing * 1.2:
            rating = "优秀 ✓"
        elif spacing <= ideal_spacing * 2:
            rating = "良好 ~"
        else:
            rating = "稀疏 ✗"

        # Lane分布（仅游侠）
        detail = ""
        if unit_type == "游侠" and 'lane' in units_here[0]:
            lanes = sorted(set(u['lane'] for u in units_here))
            detail = f"Lanes: {lanes}"

        print(f"r={radius:<5} {count:<8} {circumference:<10} {spacing:>7.1f}格   {rating:<10} {detail}")
        total_coverage += circumference

    print(f"\n总覆盖周长: {total_coverage} 格")
    print(f"平均半径: {sum(u['radius'] for u in units) / len(units):.1f}")
    print(f"最大半径: {max(u['radius'] for u in units)}")

    return by_radius


# 主逻辑
print("正在从 vps168 获取最新日志...")

result = subprocess.run(
    ['ssh', '-p', '9393', 'root@vps168', 'tail -100 /root/arenahero/arena_hero.log'],
    capture_output=True,
    text=True
)

log_text = result.stdout
rangers, vanguards, workers = extract_positions(log_text)

print(f"\n{'#'*70}")
print("# 轨道分配快照分析")
print(f"{'#'*70}")

# 分析游侠
if rangers:
    analyze_orbits(rangers, "游侠", ideal_spacing=10)

# 分析先锋
if vanguards:
    analyze_orbits(vanguards, "先锋", ideal_spacing=8)

# 分析工人
if workers:
    analyze_orbits(workers, "工人（外层轨道）", ideal_spacing=6)

print(f"\n{'='*70}")
print("分析完成！")
print(f"{'='*70}\n")
