#!/bin/bash
# 快速检查当前共享轨道状态脚本
# 当前策略使用 [orbit_assign] shared 与 lightning_*_orbit 标签；
# breakthrough/旧 NEAR-MID-FAR 标签仅属于历史日志。

echo "=== Arena Hero 轨道状态检查 ==="
echo "时间: $(date '+%Y-%m-%d %H:%M:%S')"
echo ""

# 检查进程
echo "1. 进程状态:"
ssh -p 9393 root@vps168 "ps aux | grep arena_hero | grep -v grep" || echo "  ⚠️ 进程未运行"
echo ""

# 获取最近的日志
echo "2. 最近决策（最后20行）:"
ssh -p 9393 root@vps168 "tail -20 /root/arenahero/arena_hero.log 2>/dev/null | grep -o 'tick=[0-9]*' | tail -1"
echo ""

# 统计决策类型
echo "3. 决策类型统计（最近1000行）:"
ssh -p 9393 root@vps168 "tail -1000 /root/arenahero/arena_hero.log 2>/dev/null | grep -Eo '\[orbit_assign\] shared:.*|lightning_(worker|vanguard)_orbit|mid_orbit_patrol|lightning_worker_meatshield' | sort | uniq -c | sort -rn"
echo ""

# 检查是否有错误
echo "4. 错误检查（最近100行）:"
ERROR_COUNT=$(ssh -p 9393 root@vps168 "tail -100 /root/arenahero/arena_hero.log 2>/dev/null | grep -i 'error\|exception\|traceback' | wc -l")
if [ "$ERROR_COUNT" -gt 0 ]; then
    echo "  ⚠️ 发现 $ERROR_COUNT 个错误"
    ssh -p 9393 root@vps168 "tail -100 /root/arenahero/arena_hero.log 2>/dev/null | grep -i 'error\|exception' | tail -3"
else
    echo "  ✓ 无错误"
fi
echo ""

echo "=== 检查完成 ==="
echo ""
echo "提示: 使用以下命令查看实时监控："
echo "  ssh root@vps168 'tail -f /root/arenahero/arena_hero.log' | python3 analyze_orbit_live.py /path/to/telemetry.jsonl"
