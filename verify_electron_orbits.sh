#!/bin/bash
# 验证电子排布式轨道分配

echo "=== 等待日志输出 ==="
for i in {1..10}; do
    LATEST=$(ssh -p 9393 root@vps168 "tail -100 /root/arenahero/arena_hero.log 2>/dev/null" | grep -E "orbit_assign|游侠.*→.*层" | tail -20)
    if [ -n "$LATEST" ]; then
        echo "$LATEST"
        echo ""
        echo "=== 统计各层分布 ==="
        echo "$LATEST" | grep -oP '层\K\d+' | sort | uniq -c
        break
    fi
    echo "等待中... ($i/10)"
    sleep 2
done

if [ -z "$LATEST" ]; then
    echo "❌ 未找到轨道分配日志"
    ssh -p 9393 root@vps168 "tail -50 /root/arenahero/arena_hero.log" 2>/dev/null
fi
