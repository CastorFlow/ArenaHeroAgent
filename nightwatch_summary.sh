#!/usr/bin/env bash
# 早上分析 ArenaHero 夜间值守日志。在 WSL 跑。
# 用法：./nightwatch_summary.sh [N]   N=看最近 N 条巡检（默认 200，约一夜量）
#
# 输出：
#   1. 严重/警告事件按原因聚类计数
#   2. 关键指标的时间线（每 N 条取一条，看趋势）
#   3. Claude headless 的归因段落（level=claude_analysis）原文
#   4. lifetime 累计变化（击杀/损失/采集）
#
# 全部数据来自 vps168:/root/arenahero/nightwatch.jsonl，不在本地存储。

set -u
N="${1:-200}"
REMOTE="root@vps168"
LOG="/root/arenahero/nightwatch.jsonl"

echo "=== ArenaHero 夜间值守摘要（最近 $N 条巡检）==="
echo "时间范围："
ssh -p 9393 "$REMOTE" "head -1 $LOG; tail -1 $LOG" 2>/dev/null | \
  /usr/bin/python3 -c "import sys,json;[print(' ',json.loads(l).get('ts')) for l in sys.stdin if l.strip()]" 2>/dev/null
echo

echo "=== 严重/警告事件聚类（按 reason 关键词）==="
ssh -p 9393 "$REMOTE" "tail -$N $LOG" 2>/dev/null | \
  /usr/bin/python3 -c "
import sys, json
from collections import Counter
sev=Counter(); warn=Counter()
for l in sys.stdin:
    l=l.strip()
    if not l: continue
    try: r=json.loads(l)
    except: continue
    lvl=r.get('level')
    if lvl not in ('severe','warn'): continue
    for reason in r.get('reasons',[]):
        key=reason.split('=')[0].split()[0]
        (sev if lvl=='severe' else warn)[key]+=1
print('  severe:', dict(sev) or '(none)')
print('  warn:  ', dict(warn) or '(none)')
"
echo

echo "=== 关键指标时间线（每 ~20 条采样一次）==="
ssh -p 9393 "$REMOTE" "tail -$N $LOG" 2>/dev/null | \
  /usr/bin/python3 -c "
import sys, json
rows=[]
for l in sys.stdin:
    l=l.strip()
    if not l: continue
    try: r=json.loads(l)
    except: continue
    if r.get('level')=='claude_analysis': continue
    rows.append(r)
step=max(1,len(rows)//20)
for r in rows[::step]:
    print(f\"  {r.get('ts','')} [{r.get('level')}] tick={r.get('tick')} core={r.get('core_pos')} r={r.get('core_radius')} res={r.get('resources')}/{r.get('capacity')} pop={r.get('population')} v={r.get('vanguards')} rng={r.get('rangers')} killed={r.get('lifetime',{}).get('enemy_cores_destroyed')}\")
"
echo

echo "=== Claude headless 归因段落（如有）==="
ssh -p 9393 "$REMOTE" "tail -$N $LOG" 2>/dev/null | \
  /usr/bin/python3 -c "
import sys, json
found=False
for l in sys.stdin:
    l=l.strip()
    if not l: continue
    try: r=json.loads(l)
    except: continue
    if r.get('level')=='claude_analysis':
        found=True
        print(f\"  [{r.get('ts','')}]\")
        print('   ', r.get('analysis',''))
if not found: print('  (夜间无 severe 触发 Claude 分析)')
"
echo

echo "=== 一键拿原始日志做深入分析 ==="
echo "  ssh -p 9393 $REMOTE 'tail -$N $LOG'"
