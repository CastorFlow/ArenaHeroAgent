#!/usr/bin/env python3
"""
实时监控轨道行为脚本（历史/兼容工具）。

.. deprecated::
   This monitor predates the shared Lightning orbit logs and still interprets
   retired breakthrough/NEAR/MID/FAR labels.  For current runtime behavior,
   prefer ``analyze_orbit_live.py`` or ``track_unit_orbit.py`` against telemetry.

It is intentionally kept for operators who still need to inspect historical
logs; it is not the current strategy specification.
"""

import re
import sys
from collections import defaultdict, deque
from datetime import datetime


class OrbitMonitor:
    def __init__(self):
        self.unit_positions = {}  # {unit_id: [(x, y, tick), ...]}
        self.unit_types = {}  # {unit_id: "vanguard"|"ranger"|"worker"}
        self.core_position = None
        self.last_tick = 0
        self.decision_counts = defaultdict(int)
        self.threat_events = []
        self.orbit_violations = []

    def distance(self, pos1, pos2):
        """曼哈顿距离"""
        return abs(pos1[0] - pos2[0]) + abs(pos1[1] - pos2[1])

    def parse_log_line(self, line):
        """解析日志行"""
        # 提取 tick
        tick_match = re.search(r'tick=(\d+)', line)
        if not tick_match:
            return

        tick = int(tick_match.group(1))
        self.last_tick = tick

        # 提取决策信息
        decisions_match = re.search(r'decisions=(.+?)(?:\s+\||$)', line)
        if decisions_match:
            decisions_text = decisions_match.group(1)

            # 解析各种决策
            # 格式：worker:21317ede move UP to=(474, -635)
            #      ranger:5cc9ddfc mid_orbit_patrol lane=2
            #      breakthrough:7b3fc097 flee to_core

            for decision in re.finditer(r'(\w+):([a-f0-9]+)\s+(.+?)(?:\s+\||$)', decisions_text):
                unit_type = decision.group(1)
                unit_id = decision.group(2)
                action = decision.group(3)

                # 记录单位类型
                if unit_type in ["vanguard", "ranger", "worker"]:
                    self.unit_types[unit_id] = unit_type

                # 提取位置信息
                pos_match = re.search(r'to=\((-?\d+),\s*(-?\d+)\)', action)
                if pos_match:
                    x, y = int(pos_match.group(1)), int(pos_match.group(2))
                    position = (x, y)

                    # 记录位置历史
                    if unit_id not in self.unit_positions:
                        self.unit_positions[unit_id] = deque(maxlen=10)
                    self.unit_positions[unit_id].append((x, y, tick))

                    # 检测Core位置（从Core的move中提取）
                    if "core" in unit_type.lower() or "Core" in action:
                        self.core_position = position

                # 统计决策类型
                if "breakthrough" in action:
                    if "flee" in action:
                        self.decision_counts["breakthrough:flee"] += 1
                    elif "kite" in action:
                        self.decision_counts["breakthrough:kite"] += 1
                    elif "patrol" in action:
                        self.decision_counts["breakthrough:patrol"] += 1
                    elif "approach" in action:
                        self.decision_counts["breakthrough:approach"] += 1
                elif "defend_NEAR" in action:
                    self.decision_counts["ranger:defend_NEAR"] += 1
                    self.threat_events.append(("NEAR", tick))
                elif "defend_MID" in action:
                    self.decision_counts["ranger:defend_MID"] += 1
                    self.threat_events.append(("MID", tick))
                elif "snipe_FAR" in action:
                    self.decision_counts["mid_orbit:snipe_FAR"] += 1
                    self.threat_events.append(("FAR", tick))
                elif "mid_orbit_patrol" in action:
                    self.decision_counts["mid_orbit:patrol"] += 1
                elif "meatshield" in action:
                    self.decision_counts["worker:meatshield"] += 1

    def print_status(self):
        """打印实时状态"""
        print(f"\n{'='*80}")
        print(f"轨道行为监控报告 - Tick {self.last_tick} - {datetime.now().strftime('%H:%M:%S')}")
        print(f"{'='*80}")

        # 统计单位数量
        vanguards = [uid for uid, utype in self.unit_types.items() if utype == "vanguard"]
        rangers = [uid for uid, utype in self.unit_types.items() if utype == "ranger"]
        workers = [uid for uid, utype in self.unit_types.items() if utype == "worker"]

        print("\n单位统计:")
        print(f"  先锋: {len(vanguards)} | 游侠: {len(rangers)} | 工人: {len(workers)}")
        if self.core_position:
            print(f"  Core位置: {self.core_position}")

        # 决策类型统计
        print("\n决策统计（累计）:")
        if self.decision_counts:
            for decision_type, count in sorted(self.decision_counts.items()):
                print(f"  {decision_type}: {count}")
        else:
            print("  （暂无数据）")

        # 威胁事件
        recent_threats = [t for t in self.threat_events if t[1] > self.last_tick - 100]
        if recent_threats:
            print("\n最近威胁事件（近100 tick）:")
            threat_summary = defaultdict(int)
            for threat_type, _ in recent_threats:
                threat_summary[threat_type] += 1
            for threat_type, count in sorted(threat_summary.items()):
                print(f"  {threat_type}: {count}次")

        # 轨道违规
        recent_violations = [v for v in self.orbit_violations if v["tick"] > self.last_tick - 100]
        if recent_violations:
            print("\n⚠️ 轨道违规（近100 tick）:")
            for v in recent_violations[-5:]:  # 只显示最近5条
                print(f"  Tick {v['tick']}: {v['type']} - unit={v['unit_id'][:8]} pos={v['position']}")

        # 单位位置检查
        print("\n当前单位轨道检查:")

        # 检查先锋
        if vanguards and self.core_position:
            print("  先锋（应在近轨道r=5附近）:")
            for vid in vanguards:
                if vid in self.unit_positions and self.unit_positions[vid]:
                    last_pos = self.unit_positions[vid][-1]
                    dist = self.distance((last_pos[0], last_pos[1]), self.core_position)
                    status = "✓" if abs(dist - 5) <= 3 else "✗"
                    print(f"    {status} {vid[:8]}: 距Core={dist} (期望=5±3)")

        # 检查游侠
        if rangers:
            sorted_rangers = sorted(rangers)
            print("  开路游侠（前4个，应在r=650-680范围）:")
            for rid in sorted_rangers[:4]:
                if rid in self.unit_positions and self.unit_positions[rid]:
                    last_pos = self.unit_positions[rid][-1]
                    dist = self.distance((last_pos[0], last_pos[1]), (0, 0))
                    status = "✓" if 630 <= dist <= 700 else "✗"
                    print(f"    {status} {rid[:8]}: 距原点={dist} (期望=650-680)")

            if len(rangers) > 4 and self.core_position:
                print("  中轨游侠（第5+个，应在r=10-40范围）:")
                for rid in sorted_rangers[4:]:
                    if rid in self.unit_positions and self.unit_positions[rid]:
                        last_pos = self.unit_positions[rid][-1]
                        dist = self.distance((last_pos[0], last_pos[1]), self.core_position)
                        status = "✓" if 0 <= dist <= 50 else "✗"
                        print(f"    {status} {rid[:8]}: 距Core={dist} (期望=10-40)")

        sys.stdout.flush()


def main():
    if len(sys.argv) < 2:
        print("用法: python monitor_orbit_behavior.py <log_file|stdin>")
        print("示例: ssh root@vps168 'tail -f /root/arenahero/arena_hero.log' | python monitor_orbit_behavior.py stdin")
        sys.exit(1)

    monitor = OrbitMonitor()

    if sys.argv[1] == "stdin":
        print("从标准输入读取日志，开始监控...")
        line_count = 0
        try:
            for line in sys.stdin:
                line = line.strip()
                if line:
                    monitor.parse_log_line(line)
                    line_count += 1

                    # 每50行打印一次状态
                    if line_count % 50 == 0:
                        monitor.print_status()
        except KeyboardInterrupt:
            print("\n\n监控中断，打印最终报告...")
            monitor.print_status()
    else:
        # 从文件读取
        log_file = sys.argv[1]
        print(f"从文件读取日志: {log_file}")
        with open(log_file, 'r') as f:
            for line in f:
                line = line.strip()
                if line:
                    monitor.parse_log_line(line)

        monitor.print_status()


if __name__ == "__main__":
    main()
