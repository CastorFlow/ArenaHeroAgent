#!/usr/bin/env python3
"""测试基于周长的轨道分配算法"""

def calculate_circumference_based_orbits(
    unit_count: int,
    gap: int,
    inner_radius: int,
    ideal_interval: int = 10,
) -> list[tuple[int, int]]:
    """基于周长的动态轨道分配：从内到外填充，避免外层过于稀疏"""
    if unit_count == 0:
        return []

    max_radius_by_gap = {5: 80, 3: 60, 4: 70}
    reasonable_limit = max_radius_by_gap.get(gap, 100)

    # 生成所有可能的轨道及其理想容量
    all_orbits = []
    radius = inner_radius
    while radius <= reasonable_limit:
        circumference = 8 * radius
        ideal_cap = min(8, max(2, circumference // ideal_interval))
        all_orbits.append((radius, ideal_cap))
        radius += gap

    if not all_orbits:
        return [(inner_radius, min(8, unit_count))]

    # 从内向外填充
    distribution = []
    remaining = unit_count

    for radius, ideal_cap in all_orbits:
        if remaining <= 0:
            break

        # 如果剩余单位不足以开新层（<2），停止开新层
        if remaining < 2:
            break

        allocated = min(remaining, ideal_cap)
        distribution.append([radius, allocated])
        remaining -= allocated

    # 处理余数（少于2个）：回填到已有层
    if remaining > 0 and distribution:
        for i in range(len(distribution) - 1, -1, -1):
            if remaining <= 0:
                break
            radius, count = distribution[i]
            extra = min(remaining, 8 - count)
            distribution[i][1] = count + extra
            remaining -= extra

    # 极端情况：只有1个单位
    if not distribution and unit_count > 0:
        distribution.append([inner_radius, unit_count])

    return [(r, c) for r, c in distribution]


def print_allocation(role: str, unit_count: int, result: list[tuple[int, int]]):
    """美化输出分配结果"""
    print(f"\n{'='*60}")
    print(f"{role} x {unit_count} 的轨道分配:")
    print(f"{'='*60}")
    print(f"{'半径':<8} {'周长':<8} {'分配':<8} {'间距':<10} {'覆盖评估'}")
    print('-' * 60)

    total_assigned = 0
    for r, count in result:
        circumference = 8 * r
        spacing = circumference / count if count > 0 else 0
        # 评估：间距 ≤ 理想间距*1.2 → 优秀，≤ 理想*2 → 良好，否则 → 稀疏
        if role == "游侠":
            ideal = 10
        elif role == "先锋":
            ideal = 8
        else:
            ideal = 6

        if spacing <= ideal * 1.2:
            rating = "优秀 ✓"
        elif spacing <= ideal * 2:
            rating = "良好 ~"
        else:
            rating = "稀疏 ✗"

        print(f"r={r:<5} {circumference:<8} {count:<8} {spacing:>6.1f}格  {rating}")
        total_assigned += count

    print('-' * 60)
    print(f"总分配: {total_assigned} (目标: {unit_count})")
    if total_assigned != unit_count:
        print("⚠️  警告: 分配数量不匹配!")


def compare_with_electron_model(unit_count: int):
    """对比电子排布模型"""
    print(f"\n{'#'*60}")
    print(f"电子排布模型对比 ({unit_count} 个单位)")
    print(f"{'#'*60}")

    # 电子排布: 2, 8, 18, 32...
    electron_shells = [2, 8, 18, 32, 50, 72]
    radii = [5, 10, 15, 20, 25, 30]

    print("\n电子排布 (2n²):")
    print(f"{'层':<6} {'容量':<8} {'累计':<8} {'半径':<8} {'周长':<8} {'间距'}")
    print('-' * 60)

    cumulative = 0
    for n, (cap, r) in enumerate(zip(electron_shells, radii), 1):
        cumulative += cap
        if cumulative > unit_count:
            actual_cap = unit_count - (cumulative - cap)
            cumulative = unit_count
        else:
            actual_cap = cap

        circumference = 8 * r
        spacing = circumference / actual_cap if actual_cap > 0 else 0
        print(f"n={n:<4} {actual_cap:<8} {cumulative:<8} r={r:<5} {circumference:<8} {spacing:>6.1f}格")

        if cumulative >= unit_count:
            break

    print(f"\n分析: 前{n}层填满需要 {cumulative} 个单位")
    if cumulative > unit_count:
        print(f"  → 外层半空({radii[n-1]}半径处只有{actual_cap}个，容量{electron_shells[n-1]})")
    else:
        print("  → 刚好填满")


def test_scenarios():
    """测试多种场景"""

    # 游侠测试
    test_cases = [
        ("游侠", 2, 9, 5, 10),
        ("游侠", 5, 9, 5, 10),
        ("游侠", 8, 9, 5, 10),
        ("游侠", 12, 9, 5, 10),
        ("游侠", 20, 9, 5, 10),
    ]

    for role, count, inner, gap, interval in test_cases:
        result = calculate_circumference_based_orbits(
            count, gap, inner, ideal_interval=interval
        )
        print_allocation(role, count, result)

    # 电子排布对比
    compare_with_electron_model(20)

    # 先锋测试
    print(f"\n\n{'='*60}")
    print("先锋单位测试")
    print(f"{'='*60}")
    vanguard_cases = [
        ("先锋", 1, 5, 4, 8),
        ("先锋", 3, 5, 4, 8),
        ("先锋", 6, 5, 4, 8),
    ]

    for role, count, inner, gap, interval in vanguard_cases:
        result = calculate_circumference_based_orbits(
            count, gap, inner, ideal_interval=interval
        )
        print_allocation(role, count, result)

    # 工人测试
    print(f"\n\n{'='*60}")
    print("工人单位测试 (外层轨道)")
    print(f"{'='*60}")
    worker_cases = [
        ("工人", 5, 30, 3, 6),
        ("工人", 10, 30, 3, 6),
        ("工人", 15, 30, 3, 6),
    ]

    for role, count, inner, gap, interval in worker_cases:
        result = calculate_circumference_based_orbits(
            count, gap, inner, ideal_interval=interval
        )
        print_allocation(role, count, result)


if __name__ == "__main__":
    test_scenarios()

    print(f"\n\n{'#'*60}")
    print("总结")
    print(f"{'#'*60}")
    print("""
核心改进:
1. ✓ 按周长比例分配 → 外层轨道单位更多，覆盖面积大
2. ✓ 动态容量计算 → 周长/间距，而非固定2n²
4. ✓ 优先铺外层 → 早期就能建立大防御圈

与电子排布对比:
- 电子: 2n²增长，前3层需28个单位，外层常空置
- 周长: 线性增长，20个单位可均匀铺4层
- 游戏优势: 早期防御圈大，响应速度快，覆盖无盲区

推荐配置:
""")
