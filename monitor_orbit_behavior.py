#!/usr/bin/env python3
"""
实时监控轨道行为脚本

监控目标：
1. 游侠是否按轨道巡逻（开路/中轨）
2. 先锋是否守在近轨道（r=5）
3. 威胁响应行为（NEAR/MID/FAR）
4. 开路游侠的战术响应（逃跑/游击/巡逻）
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

    def classify_ranger_role(self, unit_id, rangers_seen):
        """分类游侠角色：开路(前4) vs 中轨(第5+)"""
        sorted_rangers = sorted(rangers_seen)
        if unit_id in sorted_rangers[:4]:
            return "breakthrough"
        else:
            return "mid_orbit"

    def check_near_orbit_adherence(self, unit_id, position, tick):
        """检查先锋是否守在近轨道（r=5附近）"""
        if self.core_position is None:
            return None

        dist_to_core = self.distance(position, self.core_position)
        near_radius = 5
        tolerance = 3  # 允许±3格容差

        if dist_to_core > near_radius + tolerance:
            violation = {
                "tick": tick,
                "unit_id": unit_id,
                "type": "vanguard_too_far",
                "dist_to_core": dist_to_core,
                "expected": near_radius,
                "position": position
            }
            self.orbit_violations.append(violation)
            return False
        return True

    def check_breakthrough_orbit_adherence(self, unit_id, position, tick):
        """检查开路游侠是否在开路轨道范围内（r=650-680）"""
        origin = (0, 0)
        dist_to_origin = self.distance(position, origin)

        # 开路轨道半径范围
        inner_radius = 650
        outer_radius = 680
        tolerance = 20

        if dist_to_origin < inner_radius - tolerance or dist_to_origin > outer_radius + tolerance:
            violation = {
                "tick": tick,
                "unit_id": unit_id,
                "type": "breakthrough_out_of_range",
                "dist_to_origin": dist_to_origin,
                "expected_range": f"{inner_radius}-{outer_radius}",
                "position": position
            }
            self.orbit_violations.append(violation)
            return False
        return True

    def check_mid_orbit_adherence(self, unit_id, position, tick):
        """检查中轨游侠是否在中轨范围内（r=10-40）"""
        if self.core_position is None:
            return None

        dist_to_core = self.distance(position, self.core_position)

        # 中轨半径范围
        inner_radius = 10
        outer_radius = 40
        tolerance = 10

        if dist_to_core < inner_radius - tolerance or dist_to_core > outer_radius + tolerance:
            violation = {
                "tick": tick,
                "unit_id": unit_id,
                "type": "mid_orbit_out_of_range",
                "dist_to_core": dist_to_core,
                "expected_range": f"{inner_radius}-{outer_radius}",
                "position": position
            }
            self.orbit_violations.append(violation)
            return False
        return True

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

        print(f"\n单位统计:")
        print(f"  先锋: {len(vanguards)} | 游侠: {len(rangers)} | 工人: {len(workers)}")
        if self.core_position:
            print(f"  Core位置: {self.core_position}")

        # 决策类型统计
        print(f"\n决策统计（累计）:")
        if self.decision_counts:
            for decision_type, count in sorted(self.decision_counts.items()):
                print(f"  {decision_type}: {count}")
        else:
            print("  （暂无数据）")

        # 威胁事件
        recent_threats = [t for t in self.threat_events if t[1] > self.last_tick - 100]
        if recent_threats:
            print(f"\n最近威胁事件（近100 tick）:")
            threat_summary = defaultdict(int)
            for threat_type, _ in recent_threats:
                threat_summary[threat_type] += 1
            for threat_type, count in sorted(threat_summary.items()):
                print(f"  {threat_type}: {count}次")

        # 轨道违规
        recent_violations = [v for v in self.orbit_violations if v["tick"] > self.last_tick - 100]
        if recent_violations:
            print(f"\n⚠️ 轨道违规（近100 tick）:")
            for v in recent_violations[-5:]:  # 只显示最近5条
                print(f"  Tick {v['tick']}: {v['type']} - unit={v['unit_id'][:8]} pos={v['position']}")

        # 单位位置检查
        print(f"\n当前单位轨道检查:")

        # 检查先锋
        if vanguards and self.core_position:
            print(f"  先锋（应在近轨道r=5附近）:")
            for vid in vanguards:
                if vid in self.unit_positions and self.unit_positions[vid]:
                    last_pos = self.unit_positions[vid][-1]
                    dist = self.distance((last_pos[0], last_pos[1]), self.core_position)
                    status = "✓" if abs(dist - 5) <= 3 else "✗"
                    print(f"    {status} {vid[:8]}: 距Core={dist} (期望=5±3)")

        # 检查游侠
        if rangers:
            sorted_rangers = sorted(rangers)
            print(f"  开路游侠（前4个，应在r=650-680范围）:")
            for rid in sorted_rangers[:4]:
                if rid in self.unit_positions and self.unit_positions[rid]:
                    last_pos = self.unit_positions[rid][-1]
                    dist = self.distance((last_pos[0], last_pos[1]), (0, 0))
                    status = "✓" if 630 <= dist <= 700 else "✗"
                    print(f"    {status} {rid[:8]}: 距原点={dist} (期望=650-680)")

            if len(rangers) > 4 and self.core_position:
                print(f"  中轨游侠（第5+个，应在r=10-40范围）:")
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
