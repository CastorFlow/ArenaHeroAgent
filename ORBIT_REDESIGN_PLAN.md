> **历史文档提示：** 本文描述的是已退出运行时的轨道/突破行为，不是当前策略规范。当前行为请以 `README.md`、`docs/STRATEGY.md`、`docs/USAGE.md` 以及现行代码为准。

# 轨道重构 + 3:1 产能补兵 开发计划

> 本文件是给执行会话的开发指令书。执行会话在 ArenaHero 项目工作树 `/home/xiao/projects/ArenaHero` 下,
> 改 `arena_hero_strategy.py`(主) 和 `test_arena_hero_tactic.py`,部署到 vps168。
> **先通读全文再动手**,改一处验一处,全程 `python3 -m unittest test_arena_hero_tactic` 必须 49/49 绿。

## 0. 前置事实(已核实,不要再问)

- 运行环境:vps168,`/root/arenahero/`,venv `/root/arenahero/.venv`,SDK `arena-hero==0.2.9`,Python 3.13。
- systemd 服务 `arena-hero-agent.service`(`enabled`+`active`,**无 WatchdogSec**,见 memory `arena-hero-watchdog-gotcha`)。
- `arena_hero_strategy.py` 每 2 个 tick 热重载(改完 rsync 上去即生效,无需重启);但 `arena_hero_tactic.py` 是进程入口,改它要 `systemctl restart`。
- **已修复的关键 bug**(上一个会话已部署):`TacticMemory.load()` 的 `lightning_orbit_lanes` 反序列化现在兼容 `[r,group]` 列表和旧 `int` 两种磁盘格式。runtime 里 lanes 存 `{uid: (radius, group_idx)}` 元组。**改 orbit 逻辑时保持这个元组形式不变**,save() 不用改(元组序列化成 `[r,group]` 是对的)。
- 游戏规则无硬人口上限,成本公式 `k=max(0,floor((pop-20)/5)+1)`,`单价=round(base×1.3^k)`。100~110 是经济软天花板。105 时游侠 1349、容量 525。
- 常量真实命名(改代码时按这些名字 grep):`LIGHTNING_MAX_POPULATION=20`、`ABSOLUTE_MAX_POPULATION=100`、`LIGHTNING_BUILD_ORDER`(tuple,8 槽)、`LIGHTNING_BREAKTHROUGH_SLOT_COUNT=4`、`LIGHTNING_BREAKTHROUGH_RING_OFFSET=12`、`LIGHTNING_BREAKTHROUGH_MAX_CORE_DISTANCE=400`、`LIGHTNING_NEAR_ORBIT_RADIUS=5`、`LIGHTNING_ORBIT_LANE_GAP_RADIUS`(VG=4,RK=5,WK=3)、`LIGHTNING_IDEAL_INTERVAL`、`LIGHTNING_MIN_UNITS_PER_ORBIT=3`。
- 函数真实命名:`_lightning_calculate_outer_first_orbits`(电子排布公式,**保留**)、`_lightning_assign_orbit_lanes`(按 role 分配,**重写**)、`_lightning_orbit_waypoint`(读 lanes 算下一角,**基本不动**)、`_choose_rangers_lightning`(游侠决策,**删突破分支**)、`_choose_workers`(工人决策入口,~L3166,含 L3920 的 `lightning_worker_orbit` 分支)、`_lightning_build_slot`(槽位选兵,**重写**)、`_select_spawn`(产兵总入口,**改**)、`_lightning_defense_tier`(威胁分级,保留)、`_lightning_breakthrough_target`/`_lightning_should_breakthrough_engage`/`_lightning_find_nearby_unguarded_core`(突破相关,删调用,函数体可留作死代码或删)。
- 产兵阶梯现状:pop1→先锋,2→工人,3→游侠,4→工人,5→游侠,6→工人,7→游侠,8→工人(=1先锋4工人3游侠)。slot9+ 现状全游侠,满 20 停。`_select_spawn` 里有"资源到容量 80% 且 pop≥20 → 紧急造工人"的 `urgency_threshold` 块(**要删**)。

## 1. 目标总览

四件事,按顺序做,每件做完跑测试:

1. **取消开路/突破轨道**(4 个突破游侠回归中轨)。
2. **取消远轨,游侠+工人共用中轨**,实现"单一有序队列":游侠优先按电子排布填内层,工人接在最后一个游侠后面用同一公式排;新游侠出生→插进游侠段→挤出最靠内的工人→该工人排到全队最末尾。
3. **3:1 产能补兵 + 阵亡补同种**:pop 1-8 保留现有阶梯,slot9+ 按 3:1(游侠:工人),谁阵亡补谁(与 3:1 协同)。
4. **取消 pop-20 停产 + 取消"满仓补工人"机制**:把 `LIGHTNING_MAX_POPULATION` 提到 100,删 `urgency_threshold` 紧急工人块。

## 2. 改动 1:取消突破轨道

### 2.1 `_choose_rangers_lightning`(~L10332)

删除突破分支:
- 删 `breakthrough_safe = core_origin_dist <= ...` 和 `core_origin_dist` 计算。
- 删 `if index < LIGHTNING_BREAKTHROUGH_SLOT_COUNT and breakthrough_safe:` 整个 if-elif-else 分支(L10420 起,含 flee/kite/unguarded_core/approach/patrol)。
- 删 L10569 附近 `if index < LIGHTNING_BREAKTHROUGH_SLOT_COUNT:` 的 mid_lane 偏移分支。
- **结果**:所有游侠都走中行星轨道,`_lightning_orbit_waypoint(turn, ranger, UnitType.RANGER)`。

### 2.2 常量与死代码
- 删 `LIGHTNING_BREAKTHROUGH_SLOT_COUNT`、`LIGHTNING_BREAKTHROUGH_RING_OFFSET`、`LIGHTNING_BREAKTHROUGH_MAX_CORE_DISTANCE` 三个常量(若 grep 发现还有引用,先清引用再删)。
- `_lightning_breakthrough_target`、`_lightning_should_breakthrough_engage`、`_lightning_find_nearby_unguarded_core` 三个函数若不再被调用,删除(连同其测试若有)。**删前 grep 确认无引用**。

## 3. 改动 2:游侠+工人共用中轨(单一有序队列)

### 3.1 数据结构

`lightning_orbit_lanes` 仍按 role 存,但**中轨的半径/组号来自同一个统一分布**。新增 memory 字段(在 `TacticMemory` dataclass 加,并在 `save()`/`load()` 序列化):
- `lightning_shared_orbit_seq: dict[str, int]` —— uid → 全局队列序号(0 起,内→外)。游侠段 [0, num_ranger),工人段 [num_ranger, total)。新游侠出生→重算时游侠段扩 1,被挤出的工人序号变到 total-1。
- 旧的 `lightning_orbit_lanes[RANGER]`/`[WORKER]` 仍存 `{uid:(radius,group_idx)}`,由 seq 派生。

### 3.2 `_lightning_assign_orbit_lanes` 重写为统一队列

新逻辑(伪码):
```
def _lightning_assign_shared_middle_lanes(self, turn):
    rangers = list(turn.rangers); workers = list(turn.workers)
    total = len(rangers) + len(workers)
    if total == 0: return {}
    dist = self._lightning_calculate_outer_first_orbits(
        total, gap=5, inner=LIGHTNING_NEAR_ORBIT_RADIUS+5,
        min_units_per_orbit=3, ideal_interval=10)
    # 把分布展开成 [(radius, group_idx), ...] 全局位置序列,内→外
    positions = [(r, g) for r, cnt in dist for g in range(cnt)]
    # 游侠占前 len(rangers) 个位置,工人占后面
    seq = {}
    for i, r in enumerate(sorted(rangers, key=_uuid_key)):
        seq[str(r.id)] = i                      # 游侠序号 0..rk-1
    base = len(rangers)
    for j, w in enumerate(sorted(workers, key=_uuid_key)):
        seq[str(w.id)] = base + j               # 工人序号 rk..total-1
    # 序号 → (radius, group_idx)
    lanes = {uid: positions[idx] for uid, idx in seq.items() if idx < len(positions)}
    return lanes, seq
```

存:`lightning_orbit_lanes[Role.RANGER.value]` = 游侠的 `{uid:(r,g)}`,`lightning_orbit_lanes[Role.WORKER.value]` = 工人的。`lightning_shared_orbit_seq` = 全局 seq。

**关键:什么时候重算?**
- 游侠数变化、工人数变化、或有单位死亡 → 重算整条队列。
- 检测:对比 `self.memory.lightning_shared_orbit_seq` 里活的游侠数/工人数 vs 当前;不等就重算。
- **总数不变时不重算**(位置稳定,不抖动)。

### 3.3 "新游侠挤出工人排队尾"如何自然实现

重算时:游侠先排(序号 0..rk-1),工人接在后面(序号 rk..total-1)。游侠 rk 比上次多 1 → 游侠段占的位置往后推 1 格 → 原来在序号 rk-1 位置的工人(最靠内的工人)现在被推到 rk 位置;若 total 也 +1(造了新游侠),队列总长 +1,该工人落到 total-1(队尾)。**这正好是你要的"挤出工人排到队尾"**。验证:造新游侠前 rk=3 wk=2 total=5,工人序号 3,4;造后 rk=4 wk=2 total=6,游侠序号0-3,工人序号 4,5——原来序号3的工人现在序号4,往后挪了 1,新队尾是序号5。✓

### 3.4 `_lightning_orbit_waypoint` 几乎不动

它读 `lanes[uid]` 得 `(radius, group_index)`,算四角 phase_offset。现在 lanes 来自统一分布,group_index 仍是该半径上的序号,**但 `units_at_radius` 现在是混合的(游侠+工人同层)**。phase_offset = `group_index * 4 // units_at_radius` 对混合层也成立。**只需确认 `lightning_orbit_phase` 持久化逻辑不被破坏**。

### 3.5 工人决策入口 `_choose_workers`(L3920 的 `lightning_worker_orbit` 分支)

工人 idle(无货无资源目标)时,把 `_lightning_orbit_waypoint(turn, worker, UnitType.WORKER)` 改为查统一队列里的工人 lanes(从 `_lightning_assign_shared_middle_lanes` 拿)。`lightning_worker_meatshield`(NEAR 勤王)**保留不动**。

### 3.6 删远轨常量/逻辑
- `_lightning_assign_orbit_lanes` 里 WORKER 的 `inner_radius = ranger_outer + 3` 分支删掉(工人现在走中轨,inner=10)。
- `LIGHTNING_ORBIT_LANE_GAP_RADIUS[WORKER]=3` 是否还用?中轨统一 gap=5。但 WORKER 视野 3,若仍有别处用这个值做视野判断,**保留字典**,只在轨道分配里强制 gap=5。

## 4. 改动 3:3:1 产能 + 阵亡补同种

### 4.1 常量
- `LIGHTNING_MAX_POPULATION = 20` → `100`(经济软顶;105 也能造但极慢,留点余量)。
- `ABSOLUTE_MAX_POPULATION = 100` → `105`(硬上限兜底,防意外)。

### 4.2 重写 `_lightning_build_slot(current_population)`

```
pop 1-8: 返回 LIGHTNING_BUILD_ORDER[pop-1]  (现状不变)
pop ≥ 9: 不再用 build_slot,改用新的 ratio-aware 选择器(见 4.3)
```

### 4.3 重写 `_select_spawn` 的选兵逻辑

删 `urgency_threshold` 紧急工人块整段。新逻辑:
```
def _select_spawn(turn, projected_resources):
    pop = len(turn.units)
    if pop >= LIGHTNING_MAX_POPULATION: return None
    # pop 1-8 固定阶梯
    if pop < len(LIGHTNING_BUILD_ORDER):
        want = LIGHTNING_BUILD_ORDER[pop]   # pop 0 对应 slot1? 看现状索引
        return want if 买得起 else None
    # pop ≥ 9: 阵亡补同种优先,否则按 3:1 趋近
    died = 上次活的单位集合 - 现在活的单位集合   # 用 memory 记 last_alive_uids
    rk, wk, vg = len(turn.rangers), len(turn.workers), len(turn.vanguards)
    # 先锋维持 1
    if vg == 0 and died 含 vanguard: want = VANGUARD
    elif died:
        # 补最近阵亡的那一类(优先补缺失更严重的)
        died_rk = 死的游侠数; died_wk = 死的工人数
        if died_rk > died_wk or (died_rk==died_wk and rk/wk < 3): want = RANGER
        else: want = WORKER
    else:
        # 无阵亡,纯增长:按 3:1 趋近
        # 目标 rk/wk = 3。当前 rk/wk < 3 → 补游侠; > 3 → 补工人; ==3 → 默认补游侠(下一档 3:1)
        if wk == 0 or rk < 3*wk: want = RANGER
        elif rk > 3*wk: want = WORKER
        else: want = RANGER
    if 买得起 want: return want
    return None   # 攒钱下个 tick
```

**注意**:`died` 的判定要持久化上一 tick 的 alive uid 集合到 memory(`lightning_last_alive_uids: set[str]`,save/load 序列化)。这是"补什么兵"的依据。

### 4.4 容量与成本核对
成本用 SDK `unit_cost(type, pop)`。资源容量 `max(10,pop*5)`。pop 升到 60+ 时游侠单价上百,要留 reserve(现状 `reserve = 2 if near_threat else 0` 保留)。**不要改 reserve 逻辑**,只改选兵。

## 5. 测试要求(全部 49+ 必须绿)

### 5.1 删除/改的旧测试
- 所有引用 `breakthrough`/`LIGHTNING_BREAKTHROUGH_*` 的测试 → 删或改。
- `test_ranger_step_does_not_use_astar` → 断言改 `reason=mid_orbit_patrol`(上一会话已改,确认仍绿)。
- `test_hybrid_orbit_distribution_*` / `test_single_orbit_cap_at_8` → 上一会话已改成电子排布断言,确认仍绿。
- 旧"前 4 游侠走突破轨"相关测试 → 改成"所有游侠走中轨"。

### 5.2 新增测试
- `test_shared_orbit_rangers_inner_workers_outer`:3 游侠 2 工人 → 游侠占内层(r10/15/20),工人占外层(r25/...)。断言每个工人 radius > 每个游侠 radius。
- `test_new_ranger_pushes_worker_outward`:rk=3 wk=2 时工人序号 3,4;造第 4 个游侠后 rk=4 wk=2 → 工人序号变 4,5,原来序号 3 的工人 radius 变大(外推)。断言"被挤出的工人 radius 增大"。
- `test_stable_when_counts_unchanged`:连续两 tick 同样人数 → 同样 lanes(不抖动)。
- `test_spawn_3to1_ratio`:mock pop=9 起连续造兵,断言最终 rk:wk≈3:1(在 mock 成本下)。
- `test_spawn_replaces_dead_type`:rk=6 wk=2(正好 3:1),死一个游侠 → 下次造游侠;死一个工人 → 造工人。
- `test_spawn_keeps_one_vanguard`:先锋死了 → 下次造先锋。
- `test_no_emergency_worker_on_cap`:删 urgency 后,资源满仓且 pop<100 时不再因满仓而造工人(按 ratio 造)。
- `test_load_roundtrip_shared_seq`:save→load 后 `lightning_shared_orbit_seq` 和 lanes 完整保留(验证新字段的序列化)。

## 6. 部署 + 验证(全部测试绿后)

```
cd /home/xiao/projects/ArenaHero
python3 -m unittest test_arena_hero_tactic         # 必须 49+ 绿
python3 -m py_compile arena_hero_strategy.py arena_hero_tactic.py
rsync -av arena_hero_strategy.py arena_hero_tactic.py vps168:/root/arenahero/
# vps 上校验
ssh vps168 'md5sum /root/arenahero/arena_hero_strategy.py; /root/arenahero/.venv/bin/python -m py_compile /root/arenahero/arena_hero_strategy.py'
# 关键:load 真实 memory 不崩(memory 里有旧 lanes 格式)
ssh vps168 '/root/arenahero/.venv/bin/python -c "from pathlib import Path; import sys; sys.path.insert(0,\"/root/arenahero\"); from arena_hero_strategy import TacticMemory; m=TacticMemory.load(Path(\"/root/arenahero/.arena_hero_memory.json\")); print(\"obstacles\",len(m.known_obstacles),\"seq\",len(m.lightning_shared_orbit_seq) if hasattr(m,\"lightning_shared_orbit_seq\") else 0)"'
# strategy 是热重载,但为了让新 tactic + 新 memory 字段干净生效,restart 一次
ssh vps168 'systemctl restart arena-hero-agent && sleep 3 && systemctl is-active arena-hero-agent'
# 盯 30 秒:ticks 推进、无 Traceback
ssh vps168 'journalctl -u arena-hero-agent -f -o cat --since "30 sec ago" 2>&1 | grep -E "tick=|Traceback|Error|NameError" | head -20'
# memory 没被清空(known_obstacles 还在)
ssh vps168 '/root/arenahero/.venv/bin/python -c "from pathlib import Path; import sys; sys.path.insert(0,\"/root/arenahero\"); from arena_hero_strategy import TacticMemory; m=TacticMemory.load(Path(\"/root/arenahero/.arena_hero_memory.json\")); print(len(m.known_obstacles))"'
```

**验证通过的判据**:
1. 测试 49+ 绿。
2. vps 服务 active,NRestarts=0(不崩)。
3. tick 推进,decisions 里出现 `mid_orbit_patrol`(游侠和工人都走中轨),**不再出现 `breakthrough_*`**。
4. 随人口增长,rk:wk 趋近 3:1(telemetry 的 population 字段能算出)。
5. memory known_obstacles 不被清空(load 正常)。

## 7. 注意事项 & 边界

- **不要改 `arena_hero_tactic.py` 的热重载逻辑**(它每 2 tick 重 import strategy)。只改决策/打印。
- **WatchdogSec 绝对不能加**(见 memory `arena-hero-watchdog-gotcha`)。
- **memory 迁移**:旧 `.arena_hero_memory.json` 有 `lightning_orbit_lanes` 三 role + 旧突破字段。新增 `lightning_shared_orbit_seq` 和 `lightning_last_alive_uids` 字段,dataclass 用 `field(default_factory=...)`,load 时用 `data.get(...)` 兜底,旧文件不崩。
- **phase_offset 混合层**:同一半径上既有游侠又有工人时,四角错位仍按 `units_at_radius` 总数算。确认 `_lightning_orbit_waypoint` 的 `units_at_radius = sum(1 for (r,_) in lanes.values() if r==radius)` 取的是**合并后的 lanes**(游侠+工人),不是单 role。这是最容易漏的 bug 点,**专门写测试**。
- **成本爆炸**:pop 100 时游侠单价 1038、容量 500,造一个要攒 2+ tick。若 telemetry 出现"长期 0 产出",正常,别误判 bug。
- **工人 NEAR meatshield 与中轨冲突**:NEAR 威胁时工人回 Core 卡位(不走中轨),威胁解除后回中轨。确认 `_lightning_defense_tier` 返回 NEAR 时工人走 meatshield 分支,否则走中轨。
- vps138 是降级备用(disabled),vps168 是唯一实例,别同时跑两个(同账号单 client)。

## 8. 完成后更新 memory

- 更新 `arena-hero-deployment-vps168.md`:补充新轨道模型(无突破轨、中轨共享、3:1 产能)。
- 若发现新的 load/序列化坑,追加到 `arena-hero-orbit-lanes-save-load-bug.md` 或新建条目。
- 更新 MEMORY.md 索引。
