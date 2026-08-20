#!/usr/bin/env python3
"""实时监控VPS上的轨道分配情况"""

import re
import subprocess
import sys
from collections import defaultdict
from typing import Dict, List, Tuple

def parse_log_line(line: str) -> Dict:
    """解析日志行，提取单位位置和任务信息"""
    result = {
        'tick': None,
        'units': [],
        'rangers': [],
        'vanguards': [],
        'workers': [],
    }

    # 提取tick
    tick_match = re.search(r'tick=(\d+)', line)
    if tick_match:
        result['tick'] = int(tick_match.group(1))

    # 提取游侠信息
    ranger_matches = re.finditer(
        r'ranger:(\w+) (?:move|attack|idle) \w+ to=\((-?\d+), (-?\d+)\).*?lane=(\d+)',
        line
    )
    for match in ranger_matches:
        unit_id, x, y, lane = match.groups()
        result['rangers'].append({
            'id': unit_id[:8],
            'x': int(x),
            'y': int(y),
            'lane': int(lane),
        })

    # 提取先锋信息
    vanguard_matches = re.finditer(
        r'vanguard:(\w+) (?:move|attack|idle) \w+ to=\((-?\d+), (-?\d+)\)',
        line
    )
    for match in vanguard_matches:
        unit_id, x, y = match.groups()
        result['vanguards'].append({
            'id': unit_id[:8],
            'x': int(x),
            'y': int(y),
        })

    # 提取工人信息（外层轨道）
    worker_matches = re.finditer(
        r'worker:(\w+) move \w+ to=\((-?\d+), (-?\d+)\) goal=\((-?\d+), (-?\d+)\) reason=lightning_worker_orbit',
        line
    )
    for match in worker_matches:
        unit_id, x, y, goal_x, goal_y = match.groups()
        result['workers'].append({
            'id': unit_id[:8],
            'x': int(x),
            'y': int(y),
            'goal_x': int(goal_x),
            'goal_y': int(goal_y),
        })

    return result


def calculate_orbit_stats(units: List[Dict], core_pos: Tuple[int, int] = (539, -649)) -> Dict:
    """计算轨道统计信息"""
    if not units:
        return {}

    # 按距离分组
    orbit_groups = defaultdict(list)
    for unit in units:
        # 计算Chebyshev距离（方格地图）
        dist = max(abs(unit['x'] - core_pos[0]), abs(unit['y'] - core_pos[1]))
        orbit_groups[dist].append(unit)

    # 计算每层统计
    stats = {}
    for dist in sorted(orbit_groups.keys()):
        units_in_orbit = orbit_groups[dist]
        circumference = 8 * dist
        count = len(units_in_orbit)
        spacing = circumference / count if count > 0 else 0

        stats[dist] = {
            'count': count,
            'circumference': circumference,
            'spacing': spacing,
            'unit_ids': [u['id'] for u in units_in_orbit],
        }

    return stats


def display_orbit_distribution(tick: int, rangers: List[Dict], vanguards: List[Dict], workers: List[Dict]):
    """显示轨道分配情况"""
    print(f"\n{'='*70}")
    print(f"Tick {tick} 轨道分配快照")
    print(f"{'='*70}")

    # 游侠轨道
    if rangers:
        print(f"\n【游侠轨道】 (总计 {len(rangers)} 个)")
        print(f"{'半径':<8} {'单位数':<8} {'周长':<8} {'间距':<10} {'评估':<8} {'Lane分布'}")
        print('-' * 70)

        ranger_stats = calculate_orbit_stats(rangers)
        for radius in sorted(ranger_stats.keys()):
            stat = ranger_stats[radius]
            spacing = stat['spacing']

            # 评估（理想间距10格）
            if spacing <= 12:
                rating = "优秀 ✓"
            elif spacing <= 20:
                rating = "良好 ~"
            else:
                rating = "稀疏 ✗"

            # Lane分布
            lanes = [r['lane'] for r in rangers if max(abs(r['x'] - 539), abs(r['y'] - (-649))) == radius]
            lane_dist = ', '.join(f"L{lane}" for lane in sorted(lanes))

            print(f"r={radius:<5} {stat['count']:<8} {stat['circumference']:<8} {spacing:>6.1f}格  {rating:<8} {lane_dist}")

    # 先锋轨道
    if vanguards:
        print(f"\n【先锋轨道】 (总计 {len(vanguards)} 个)")
        print(f"{'半径':<8} {'单位数':<8} {'周长':<8} {'间距':<10} {'评估'}")
        print('-' * 70)

        vanguard_stats = calculate_orbit_stats(vanguards)
        for radius in sorted(vanguard_stats.keys()):
            stat = vanguard_stats[radius]
            spacing = stat['spacing']

            # 评估（理想间距8格）
            if spacing <= 9.6:  # 8 * 1.2
                rating = "优秀 ✓"
            elif spacing <= 16:  # 8 * 2
                rating = "良好 ~"
            else:
                rating = "稀疏 ✗"

            print(f"r={radius:<5} {stat['count']:<8} {stat['circumference']:<8} {spacing:>6.1f}格  {rating}")

    # 工人外层轨道
    if workers:
        print(f"\n【工人外层轨道】 (总计 {len(workers)} 个在轨)")
        print(f"{'半径':<8} {'单位数':<8} {'周长':<8} {'间距':<10} {'评估'}")
        print('-' * 70)

        worker_stats = calculate_orbit_stats(workers)
        for radius in sorted(worker_stats.keys()):
            stat = worker_stats[radius]
            spacing = stat['spacing']

            # 评估（理想间距6格）
            if spacing <= 7.2:  # 6 * 1.2
                rating = "优秀 ✓"
            elif spacing <= 12:  # 6 * 2
                rating = "良好 ~"
            else:
                rating = "稀疏 ✗"

            print(f"r={radius:<5} {stat['count']:<8} {stat['circumference']:<8} {spacing:>6.1f}格  {rating}")


def main():
    """主函数：SSH到VPS并实时监控"""
    print("连接到 vps168，监控轨道分配...")
    print("按 Ctrl+C 退出")

    cmd = [
        'ssh', '-p', '9393', 'root@vps168',
        'tail -f /root/arenahero/arena_hero.log'
    ]

    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1
        )

        last_tick = None
        for line in process.stdout:
            line = line.strip()
            if not line or 'tick=' not in line:
                continue

            data = parse_log_line(line)
            if data['tick'] is None:
                continue

            # 每10个tick显示一次
            if last_tick is None or data['tick'] - last_tick >= 10:
                if data['rangers'] or data['vanguards'] or data['workers']:
                    display_orbit_distribution(
                        data['tick'],
                        data['rangers'],
                        data['vanguards'],
                        data['workers']
                    )
                    last_tick = data['tick']

    except KeyboardInterrupt:
        print("\n\n监控已停止")
        process.terminate()
        sys.exit(0)
    except Exception as e:
        print(f"\n错误: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
