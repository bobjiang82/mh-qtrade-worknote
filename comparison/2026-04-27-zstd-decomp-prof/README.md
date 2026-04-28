# Round-4: zstd 解压 profiling — NUMA binding + 真实频率/IPC/功耗

实验日期: 2026-04-27
作用域: 单核解压, NUMA 绑定, turbostat 真实频率
输入: silesia.tar 经 zstd 1.5.7 单线程压缩生成的 reference frames (跨机字节一致, sha256 校验通过, 详见 round-3)
zstd 二进制: 1.5.7 (与 round-3 同一份, sha256=bd96ed25...)

## 实验设计

第三轮已经把"frame 字节一致 + 1.5.7 版本 + N=20 latency 分布"对齐, 解压差距收敛到 1% 以内.
第四轮在此基础上加两层观察:

1. NUMA + 单核绑定: `numactl --cpunodebind=0 --membind=0 taskset -c 0 zstd -d -q ...`
   - 9965 是单 NUMA 节点 (192 核 1 socket), 绑定无跨节点开销.
   - 6979P 有 6 个 NUMA 节点 (2 socket × 3), node0 有 80 个逻辑 CPU (含 cpu0).
2. 双层频率采样:
   - Python 5ms 采 `/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq` (governor 视角)
   - turbostat 1s × 5 间隔采 APERF/MPERF 算 Bzy_MHz + IPC + PkgWatt (真实频率)
3. 仅解压, level = 1/9/19/22, 每 level N=20.

## 关键结果

### 解压延迟 (中位, 单核 cpu0, NUMA pinned, N=20)

- 9965  L1=152.7ms  L9=164.3ms  L19=181.3ms  L22=216.7ms
- 6979P L1=259.2ms  L9=282.1ms  L19=308.0ms  L22=322.1ms
- 9965 在所有 level 上比 6979P 快约 33-41%.

p95 与中位差 < 5%, 单核解压非常稳定.

### 真实频率 / IPC / 功耗 (turbostat, busy 区间, IPC>=2.0 过滤)

L9:
- 9965  Bzy=3661 MHz  IPC=3.67  PkgWatt=53.1 W
- 6979P Bzy=3900 MHz  IPC=2.55  PkgWatt=169 W (整 socket, 多核背景)

L19:
- 9965  Bzy=3687 MHz  IPC=3.44  PkgWatt=54.5 W
- 6979P Bzy=3788 MHz  IPC=2.72  PkgWatt=195 W

L22:
- 9965  Bzy=3679 MHz  IPC=3.35  PkgWatt=50.1 W
- 6979P Bzy=3900 MHz  IPC=2.28  PkgWatt=165 W

### 核心观察

1. 6979P 单核标称 turbo 3.9 GHz, 实际跑满; 9965 实测 3.66-3.69 GHz (低于其 5.0 GHz boost 标称, 可能 BIOS 设的功耗/频率上限).
2. 即使 6979P 频率高 6-7%, 9965 IPC 高 30-50% (3.4-3.7 vs 2.3-2.7), 这是它解压更快的主因 — zstd 解压是分支密集 + 顺序内存访问, AMD Zen5 前端 + L2 看起来更适配.
3. PkgWatt 不可直接比: 9965 数字是 50 W 量级, 6979P 是 165-195 W, 但后者是整 socket 含 127 个其他空闲核背景, 不是 zstd 进程本身功耗.
4. 9965 turbostat 不报 CoreTmp/RAMWatt (AMD ESMI 默认未启用), 只能拿到 PkgWatt + Bzy + IPC.

### NUMA 绑定代价 (Round-3 无 pin vs Round-4 pinned)

- 9965 (单 NUMA): 解压 latency 几乎不变 (L22: 215 → 217ms, +1%). 符合预期.
- 6979P (6 NUMA): 解压 latency 全面变慢 — L1 170→259ms (+52%), L22 208→322ms (+55%).
  - 把进程钉死在 cpu0 + node0 后, 失去了 OS 调度器自动选最快核 + L3 复用的机会.
  - 在多 NUMA Intel 平台上, 单核绑定不一定是"更纯净"的测法, 反而可能制造 cache/prefetch 劣势.

## 文件清单

- decomp-9965/decomp_freq.csv     — 9965 N=20 latency + scaling_cur_freq 统计
- decomp-9965/turbostat/*.txt      — L9/L19/L22 真实频率 IPC PkgWatt
- decomp-9965/system.txt           — lscpu 摘要
- decomp-9965/info.json            — run metadata (host/uname/zstd version)
- decomp-6979P/...                 — 同上结构
- scripts/run_decomp_freq.py       — Python 采样脚本
- scripts/run_turbostat.sh         — turbostat 包装脚本
- plots/latency_median.png         — latency 对比
- plots/turbostat_compare.png      — Bzy_MHz / IPC / PkgWatt 三联图
- plots/round3_vs_round4.png       — 无 pin vs pinned 影响

## 注意事项

- 9965 的 scaling_cur_freq 在 intel_pstate 等驱动下可能只是 governor 目标值, 真实频率以 turbostat Bzy_MHz 为准.
- PkgWatt 是 socket 级数据, 含背景噪声, 不要做绝对值跨机对比.
- turbostat 5 个采样点中只有 IPC>=2.0 的算入 busy 区间, 排除间歇 idle.
- Round-3 数字是"单线程无 NUMA pin"的近似估计 (从前次 latency.csv 取中位), 用于趋势对比, 不要当精确 baseline.
