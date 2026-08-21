#!/bin/bash
# 验证当前共享轨道分配
# 兼容当前日志格式：[orbit_assign] shared: ... distribution=...

echo "=== 等待共享轨道日志输出 ==="
for i in {1..10}; do
    LATEST=$(ssh -p 9393 root@vps168 "tail -100 /root/arenahero/arena_hero.log 2>/dev/null" | grep -E "\[orbit_assign\] shared:" | tail -20)
    if [ -n "$LATEST" ]; then
        echo "$LATEST"
        echo ""
        echo "=== 当前共享轨道分配 ==="
        echo "$LATEST" | sed -n 's/.*distribution=//p'
        break
    fi
    echo "等待中... ($i/10)"
    sleep 2
done

if [ -z "$LATEST" ]; then
    echo "❌ 未找到当前共享轨道分配日志"
    ssh -p 9393 root@vps168 "tail -50 /root/arenahero/arena_hero.log" 2>/dev/null
fi
