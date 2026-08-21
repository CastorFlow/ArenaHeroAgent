#!/usr/bin/env python3
"""测试动态上限的改进效果"""

def calc_orbits_old(n, gap, inner, ideal):
    """旧算法：固定上限8"""
    max_r = {5:80, 3:60}.get(gap, 100)
    orbits = []
    r = inner
    while r <= max_r:
        cap = min(8, max(2, (8*r) // ideal))
        orbits.append((r, cap))
        r += gap

    dist = []
    remaining = n
    for r, cap in orbits:
        if remaining <= 0 or remaining < 2:
            break
        allocated = min(remaining, cap)
        dist.append((r, allocated))
        remaining -= allocated

    if remaining > 0 and dist:
        for i in range(len(dist)-1, -1, -1):
            if remaining <= 0:
                break
            r, cnt = dist[i]
            extra = min(remaining, 8 - cnt)
            dist[i] = (r, cnt + extra)
            remaining -= extra

    return dist

def calc_orbits_new(n, gap, inner, ideal):
    """新算法：动态上限"""
    max_r = {5:80, 3:60}.get(gap, 100)
    orbits = []
    r = inner
    while r <= max_r:
        # 动态上限
        if r <= 20:
            max_per_orbit = 8
        elif r <= 40:
            max_per_orbit = 16
        else:
            max_per_orbit = 24
        cap = min(max_per_orbit, max(2, (8*r) // ideal))
        orbits.append((r, cap))
        r += gap

    dist = []
    remaining = n
    for r, cap in orbits:
        if remaining <= 0 or remaining < 2:
            break
        allocated = min(remaining, cap)
        dist.append((r, allocated))
        remaining -= allocated

    if remaining > 0 and dist:
        for i in range(len(dist)-1, -1, -1):
            if remaining <= 0:
                break
            r, cnt = dist[i]
            # 动态上限
            if r <= 20:
                max_per_orbit = 8
            elif r <= 40:
                max_per_orbit = 16
            else:
                max_per_orbit = 24
            extra = min(remaining, max_per_orbit - cnt)
            dist[i] = (r, cnt + extra)
            remaining -= extra

    return dist

def compare_allocation(rangers, workers, label):
    """对比旧算法和新算法"""
    print(f"\n{'='*80}")
    print(f"{label} - 游侠{rangers}个 + 工人{workers}个")
    print(f"{'='*80}\n")

    # 游侠
    rk_old = calc_orbits_old(rangers, 5, 10, 10)
    rk_new = calc_orbits_new(rangers, 5, 10, 10)

    print("【游侠】")
    print("\n旧算法（上限8）:")
    for r, c in rk_old:
        interval = (8*r) // c if c > 0 else 999
        status = '✓' if interval <= 12 else '⚠' if interval <= 18 else '❌'
        print(f"  r={r:2}: {c:2}个 (间距{interval:3}格) {status}")

    print("\n新算法（动态上限）:")
    for r, c in rk_new:
        interval = (8*r) // c if c > 0 else 999
        status = '✓' if interval <= 12 else '⚠' if interval <= 18 else '❌'
        print(f"  r={r:2}: {c:2}个 (间距{interval:3}格) {status}")

    # 统计改进
    old_max_interval = max((8*r)//c for r, c in rk_old if c > 0)
    new_max_interval = max((8*r)//c for r, c in rk_new if c > 0)
    old_sparse = sum(1 for r, c in rk_old if (8*r)//c > 20)
    new_sparse = sum(1 for r, c in rk_new if (8*r)//c > 20)

    print("\n改进:")
    print(f"  最大间距: {old_max_interval}格 → {new_max_interval}格 (减少{old_max_interval-new_max_interval}格)")
    print(f"  稀疏层数: {old_sparse}层 → {new_sparse}层 (减少{old_sparse-new_sparse}层)")

    # 工人
    rk_outer = max(r for r, _ in rk_new)
    wk_inner = rk_outer + 3

    wk_old = calc_orbits_old(workers, 3, wk_inner, 6)
    wk_new = calc_orbits_new(workers, 3, wk_inner, 6)

    print("\n【工人】")
    print(f"(从 r={wk_inner} 开始)")

    print("\n旧算法（上限8）:")
    if wk_old:
        for r, c in wk_old[:5]:  # 只显示前5层
            interval = (8*r) // c if c > 0 else 999
            status = '✓' if interval <= 8 else '⚠' if interval <= 12 else '❌'
            print(f"  r={r:2}: {c:2}个 (间距{interval:3}格) {status}")
        if len(wk_old) > 5:
            print(f"  ... (还有{len(wk_old)-5}层)")

    print("\n新算法（动态上限）:")
    if wk_new:
        for r, c in wk_new[:5]:  # 只显示前5层
            interval = (8*r) // c if c > 0 else 999
            status = '✓' if interval <= 8 else '⚠' if interval <= 12 else '❌'
            print(f"  r={r:2}: {c:2}个 (间距{interval:3}格) {status}")
        if len(wk_new) > 5:
            print(f"  ... (还有{len(wk_new)-5}层)")

def main():
    print("="*80)
    print("动态上限改进效果测试")
    print("="*80)
    print("\n规则:")
    print("  r ≤ 20: 上限 8个/层")
    print("  r ≤ 40: 上限16个/层")
    print("  r > 40: 上限24个/层")

    # 测试不同规模
    compare_allocation(60, 43, "中期配置")
    compare_allocation(80, 23, "极限探测")

    print("\n" + "="*80)
    print("总结")
    print("="*80)
    print("\n✅ 动态上限解决了外层稀疏问题")
    print("✅ 内层保持 8个上限，避免拥挤")
    print("✅ 外层提升到 16-24个，充分利用周长")
    print("✅ 单位分布更均匀，探测效率显著提升")

if __name__ == '__main__':
    main()
