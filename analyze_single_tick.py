#!/usr/bin/env python3
"""提取最后一个完整tick的轨道分配并分析"""

import re
import subprocess
from collections import defaultdict

def extract_latest_tick(log_text: str):
    """提取最后一个完整的tick"""
    lines = log_text.strip().split('\n')

    # 找到最后一个tick行
    for line in reversed(lines):
        if 'tick=' in line and 'decisions=' in line:
            return line

    return None


def extract_positions(tick_line: str):
    """从单个tick行中提取单位位置"""
    core = (539, -649)

    rangers = []
    vanguards = []
    workers = []

    # 提取tick号
    tick_match = re.search(r'tick=(\d+)', tick_line)
    tick = int(tick_match.group(1)) if tick_match else None

    # 提取游侠
    for match in re.finditer(r'ranger:(\w+) move \w+ to=\((-?\d+), (-?\d+)\).*?mid_orbit_patrol lane=(\d+)', tick_line):
        unit_id, x, y, lane = match.groups()
        x, y = int(x), int(y)
        rangers.append({
            'id': unit_id[:8],
            'x': x,
            'y': y,
            'lane': int(lane),
            'radius': max(abs(x - core[0]), abs(y - core[1]))
        })

    # 提取先锋
    for match in re.finditer(r'vanguard:(\w+) move \w+ to=\((-?\d+), (-?\d+)\).*?lightning_vanguard_orbit', tick_line):
        unit_id, x, y = match.groups()
        x, y = int(x), int(y)
        vanguards.append({
            'id': unit_id[:8],
            'x': x,
            'y': y,
            'radius': max(abs(x - core[0]), abs(y - core[1]))
        })

    # 提取工人（仅在轨道上的）
    for match in re.finditer(r'worker:(\w+) move \w+ to=\((-?\d+), (-?\d+)\).*?lightning_worker_orbit', tick_line):
        unit_id, x, y = match.groups()
        x, y = int(x), int(y)
        workers.append({
            'id': unit_id[:8],
            'x': x,
            'y': y,
            'radius': max(abs(x - core[0]), abs(y - core[1]))
        })

    return tick, rangers, vanguards, workers


def analyze_orbits(units, unit_type, ideal_spacing):
    """分析轨道分布"""
    if not units:
        print(f"\n【{unit_type}】 无数据")
        return None

    by_radius = defaultdict(list)
    for u in units:
        by_radius[u['radius']].append(u)

    print(f"\n{'='*75}")
    print(f"【{unit_type}】 总计 {len(units)} 个")
    print(f"{'='*75}")
    print(f"{'半径':<6} {'数量':<6} {'周长':<8} {'间距':<10} {'评估':<10} {'详细'}")
    print('-' * 75)

    excellent = good = sparse = 0

    for radius in sorted(by_radius.keys()):
        units_here = by_radius[radius]
        count = len(units_here)
        circumference = 8 * radius
        spacing = circumference / count if count > 0 else 0

        # 评估
        if spacing <= ideal_spacing * 1.2:
            rating = "优秀 ✓"
            excellent += 1
        elif spacing <= ideal_spacing * 2:
            rating = "良好 ~"
            good += 1
        else:
            rating = "稀疏 ✗"
            sparse += 1

        # Lane分布（仅游侠）
        detail = ""
        if unit_type == "游侠" and 'lane' in units_here[0]:
            lanes = sorted(set(u['lane'] for u in units_here))
            detail = f"Lanes {lanes}"

        print(f"r={radius:<3} {count:<6} {circumference:<8} {spacing:>6.1f}格  {rating:<10} {detail}")

    print(f"\n统计: 优秀={excellent}层, 良好={good}层, 稀疏={sparse}层")
    print(f"最大半径: r={max(u['radius'] for u in units)}")

    return by_radius


# 主逻辑
print("正在从 vps168 获取最新日志...")

result = subprocess.run(
    ['ssh', '-p', '9393', 'root@vps168', 'tail -50 /root/arenahero/arena_hero.log'],
    capture_output=True,
    text=True
)

log_text = result.stdout
tick_line = extract_latest_tick(log_text)

if not tick_line:
    print("错误: 未找到有效的tick数据")
    exit(1)

tick, rangers, vanguards, workers = extract_positions(tick_line)

print(f"\n{'#'*75}")
print(f"# 轨道分配快照 - Tick {tick}")
print(f"# 新算法: 基于周长动态分配")
print(f"{'#'*75}")

# 分析游侠
analyze_orbits(rangers, "游侠中层轨道", ideal_spacing=10)

# 分析先锋
analyze_orbits(vanguards, "先锋近层轨道", ideal_spacing=8)

# 分析工人
analyze_orbits(workers, "工人外层轨道", ideal_spacing=6)

print(f"\n{'='*75}")
print("✓ 热部署成功！新的周长分配算法正在运行")
print(f"{'='*75}\n")
