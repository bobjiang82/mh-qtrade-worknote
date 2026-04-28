# Worknote

> 工作笔记：每次实验/排查/学习的简要记录。最新条目在最上方。

---

# 2026-04-28 — ZSTD 第五轮：客户量化数据多线程 pipeline 解压 (字典 vs 无字典)

模拟客户实际场景：行宽 203B (10 int64 + 15 double + 3 uint8)，5 行/block ≈ 1KB，多文件并行解压。三阶段 pipeline (Prefetcher → Decompressor → 隐式 merger)，BATCH=256 frames per queue item，per-thread `ZstdDecompressor` + 共享 `ZstdCompressionDict`。

测试矩阵：F prefetch ∈ {1,3,6} × D decomp ∈ {F, 2F} × dict ∈ {no, yes} × cache ∈ {warm, cold} × pin ∈ {default, numa}（共 14 cell × 2 机）。数据集 6 文件 × 200 万 block ≈ 12 GB raw / 机。

## 三个核心数字

- **字典压缩比 +26%**：1KB block 上 no-dict 1.515x → dict (64 KB, 1 万样本训) 1.907x。客户传输成本立省 21%。
- **6979P 多线程胜出**：F=3 D=3 dict warm，6979P 277 MB/s vs 9965 142 MB/s。9965 单线程更快 (246 vs 238 MB/s)，但多线程被 Ubuntu 24.04 自带 zstandard 0.22 老 binding 拖累；6979P 上 0.25 较新版 GIL 释放更细。
- **NUMA interleave 在 6979P 上 +40%**：F=6 D=12 dict warm，default 107 → numactl --interleave=all 150 MB/s。多 NUMA Intel 平台上字典访问跨 node，interleave 才是正确姿势；与第四轮"single-node hard pin 反而慢"完全对应。

## 实现要点

- 文件格式：连续 `[u32 LE length][zstd frame]`，自描述 frame 边界，无需独立 index。
- gen_quant_blocks.py 按字段语义生成（timestamp 单调+抖动 / sym_id randint / price cumsum 高斯 round 4 位 / qty lognormal round / flag 偏分布），保证可压性贴近真实。
- 字典训练：`ZstdCompressor.train_dictionary(dict_size=65536, samples=10000)`，0.6s 训完，两机共用一份字典。
- pipeline 关键：BATCH=256 入队，否则 1KB frame 的 queue 锁开销吃掉 GIL 释放收益。
- cold cache：`echo 3 > /proc/sys/vm/drop_caches`。结果 cold/warm 差 ≤ 4%（NVMe + 4 MB sequential read 充分）。

## 给客户的建议

1. 1KB block 量化数据务必训字典：64 KB 字典 1 万样本即可，传输成本 -21%。
2. Python 解压上限 ≈ 250 MB/s 单线程，多线程 scaling 差；生产用 C++ 可期待 3-5×。
3. 多 NUMA Intel 平台一律 `numactl --interleave=all`，不要 single-node hard pin。
4. 多文件并行 F=3..6 是最稳吞吐区间，再多 prefetch 受限于 Python queue 锁不再线性。

详细：`comparison/2026-04-28-zstd-quant-pipeline/`

---

# 2026-04-28 — ZSTD 第四轮：解压 profiling — NUMA 绑定 + 真实频率/IPC/功耗

第三轮把跨机解压差距收敛到 1% 以内（frame 字节对齐 + 1.5.7 同版本）。第四轮加两层观察：

- **NUMA + 单核绑定**：`numactl --cpunodebind=0 --membind=0 taskset -c 0`，把进程钉到 cpu0 + node0。
- **真实频率/IPC/功耗**：除了 Python 5ms 采 `scaling_cur_freq`，还跑 turbostat 取 APERF/MPERF 的 `Bzy_MHz` 和 `IPC`、整 socket 的 `PkgWatt`（governor 目标值会骗人，turbostat 才是真的）。

只测解压，level=1/9/19/22，N=20。

## 三个核心数字

- **9965 解压全面快 33-41%**：silesia.tar 单核解压中位 L1=153ms / L9=164ms / L19=181ms / L22=217ms；6979P L1=259ms / L9=282ms / L19=308ms / L22=322ms。
- **频率打平、IPC 拉开**：6979P 单核 turbo 跑满 3.9 GHz，9965 实测 3.66-3.69 GHz（被 BIOS 限到接近基频，离 5.0 GHz boost 标称差很远）。即便如此，9965 IPC 高 30-50%（3.4-3.7 vs 2.3-2.7）——zstd 解压是分支密集 + 顺序内存读，看起来更适配 Zen5 前端 + L2。
- **NUMA pin 在 6979P 上反而更慢**：6979P 6 NUMA，单核绑定后解压 latency 普遍 +50%（L22: 208ms → 322ms）。9965 单 NUMA，无变化。多 NUMA 平台上"绑定 = 更纯净"是错觉。

## 注意事项

- 9965 的 turbostat 不报 `CoreTmp` 和 `RAMWatt`（AMD ESMI 默认未启用），只能拿到 `PkgWatt + Bzy_MHz + IPC`。
- `PkgWatt` 是整 socket，6979P 数字含 127 个其他空闲核背景，不要直接对比绝对值。

详见 `comparison/2026-04-28-zstd-decomp-prof/README.md`。

# 2026-04-27 — ZSTD 第三轮：版本对齐 1.5.7 + 字节相同 reference frame + 延迟分布

继续修订前两轮的结论。这一轮把可比性做到位：

- **zstd 版本统一到 1.5.7**：在 9965 自编一个 `zstd157`，scp 到两机使用，sha256 双方一致 (`bd96ed25...4f10b63`)。前两轮 9965 跑 1.5.5、6979P 跑 1.5.1，是污染源。
- **解压输入字节对齐**：在 9965 用 1.5.7 单线程压出 L1/L9/L19/L22 五个 reference frame，scp 到 6979P sha256 双校验。前两轮"各机各自压再各自解"，frame 不同字节，差距其实包含了切分差异。
- **延迟分布**：N=20 次外层 wrap 计时，记录 p50/p90/p99/max/std。
- **Block size**：用 `zstd -lv` dump 了 frame header；zstd block max 固定 128 KiB（`ZSTD_BLOCKSIZE_MAX`），随 level 变的是 Window Size（L1=512K / L9=4M / L19=8M / L22=128M）。

→ 还顺手发现一个 bug：上一轮 6979P 上的 silesia_shuf.tar sha256 是 `79cd99...`，跟 9965 上的 `ebfcba3c...` 不一样。本轮已对齐。也就是说**前两轮压缩侧两机用的输入也不完全一样**——影响有限（同源 silesia + 同 seed shuffle，可能只是打包 metadata 差异），但严格说前两轮的"压缩对比"也有这一污染。

详见 `comparison/2026-04-27-zstd-latency-aligned/README.md`。

## 关键修订（重点）

| 指标 | 第一轮 (text.bin) | 第二轮 (silesia 旧 zstd) | **第三轮 (对齐)** |
|---|---|---|---|
| L19 单核压缩 | (不可比) | 6979P 快 ~21% | **6979P 快 2.4%** |
| L22 单核压缩 | (不可比) | 6979P 快 ~23% | **6979P 快 8%** |
| L1 单核压缩 | (不可比) | (没分项) | **6979P 快 31%** |
| L19 解压 | 9965 快 37% | 9965 快 8% | **持平 (<1%)** |
| L19 T=8 压缩 | (无) | (无) | **9965 快 14%，std 小 8 倍** |

## 综合结论（最新）

1. **解压性能两机本质上一致**。前两轮"9965 解压领先 8%~37%"是版本 + frame 不同造成的伪差距。
2. **压缩侧 6979P 仅在 L1（短匹配主导）有显著优势 (31%)**。中高 level (L9~L22) 单核压缩两机差距大幅缩小到 0~8%。
3. **多线程压缩 L19 T=8 9965 反超 14%，且方差小一个量级**：9965 在多线程一致性上明显占优。这是本轮新发现，前两轮无此对比。
4. **延迟分布上 6979P 压缩侧肥尾更明显**：std 普遍是 9965 的 2~3 倍。除了硬件本身，6979P 同机有 408 GiB 背景占用也有贡献。
5. **方法论教训**：跨机性能对比必须先做版本对齐 + 输入字节校验 + 解压输入字节统一，否则结论可能完全是伪的。前两轮就是反面教材。

---

# 2026-04-27 — ZSTD silesia 固定语料复测（修订上一节结论）

承接下面 cross-server 对比中"text.bin 不可比"的 caveat，这次换成业界标准 silesia 语料（打乱后做成 211 MB 的 `silesia_shuf.tar`，两机使用同一份 sha256 校验文件 `ebfcba3c…`），让 ratio 真正可比。

详见 `comparison/2026-04-27-zstd-silesia/README.md`。

## 与上一轮 (text.bin 自重复) 的关键差异

| 维度                       | 上一轮 (text.bin)        | 本轮 (silesia)              | 结论是否变化           |
|----------------------------|--------------------------|------------------------------|-----------------------|
| 两机 ratio 可比             | 否 (2.53 vs 4.55)        | 是 (4.04 vs 4.04)            | 修复                  |
| L19 单核压缩速度对比         | 几乎一致 (~5.2 MB/s)     | 6979P 快 21% (4.33 vs 3.58)  | **变化**：压缩 IPC 6979P 占优 |
| L19 单核解压对比             | 9965 快 37%              | 9965 快 8%                   | **变化**：差距大幅收窄 |
| L19 多线程饱和点             | T=32, 顶峰 ~80~100 MB/s  | T=8, 顶峰 ~15 MB/s           | **变化**：211 MB 数据量限制 |

## 修订之前的结论

- **L19 单核压缩 6979P 快 ~21%**（4.33 vs 3.58 MB/s）。上一轮 "两机几乎一致 ~5.2 MB/s" 是 text.bin 重复结构造成的 artifact — 9965 的 long-range matcher 在 seed 重复上有戏可唱，抹掉了 IPC 差距；silesia 真实文本下纯 IPC 差距浮现，6979P 确实快。
- **L19 单核解压 9965 只快 ~8%**（1362 vs 1260 MB/s），远不像上一轮 text.bin 显示的 37%。上一轮 1.4× ~ 4× 的大差距来自 random / zeros / 重复文本这些极端 cache-friendly 场景，不是 typical 真实负载。
- **多线程饱和点完全是数据大小决定的**：1 GiB text.bin 在 T=32 饱和、~100 MB/s；211 MB silesia 在 T=8 饱和、~15 MB/s。硬件能跑多少核与单文件 zstd 多线程能用到多少核基本无关。要充分用 100+ 核必须并行多文件。

## 综合结论（这一轮才是真的）

在 silesia 这类典型真实数据上 **6979P 略占优**：高 level 压缩快约 20%，解压略慢 ~7%。EPYC 9965 在极端 memcpy / cache-friendly 场景仍有压倒性优势（上一轮 random/zeros 的 3-4×），但这种场景在生产中不常见。

## 教训

- **不要用 cat /usr 拼出来再自重复填充的"伪文本"做跨机基准**。窗口里的重复结构会让两台机器各自落入不同的算法快路径，结果完全不可比。
- 跨机测压缩比 / 速度，必须确认两机使用 sha256 一致的同一份输入。

---



# 2026-04-27 — ZSTD 跨服务器对比 (9965 vs 6979P)

在第二台服务器 `6979P (10.239.23.11)` 上跑了和昨天 (105) 同一套 zstd matrix，做横向对比。

## 第二台机环境
- CPU: Intel Xeon 6979P, 480 逻辑线程
- 内存: 754 GiB（测试时 ~408 GiB 已被其他进程占用，剩余 346 GiB 可用）
- zstd: v1.5.1（注意：105 上是 1.5.5，1.5.x 之间解压器无重大变更，认为可比）
- 工作目录: `/home/bjiang7/zstd-bench/`

## 主要差异（节选，详见 comparison/2026-04-27-zstd-cross-server/）

L19 单线程基线：

| 指标                | 9965 (EPYC 9965) | 6979P (Xeon 6979P) |
|---------------------|------------------------|-------------------------|
| text.bin compress    |  5.19 MB/s             |  5.20 MB/s              |
| text.bin decompress  |  1,951 MB/s            |  1,419 MB/s             |
| random.bin decomp    | 24,377 MB/s            |  8,424 MB/s             |
| zeros.bin  decomp    | 51,835 MB/s            | 12,552 MB/s             |

L19 多线程压缩（text.bin）顶峰：105 在 T=32 ~103 MB/s；11 在 T=32 ~79 MB/s。两机都在 T=32 饱和，再加线程没收益。

## 三个结论
1. **高 level 单核压缩两机几乎一致** — 受算法 CPU 路径主导，IPC 差距体现不出来。
2. **解压（cache/memcpy bound）EPYC 9965 全面领先 1.4×~4×** — 单核内存子系统更强。
3. **想用满 100+ 核必须多文件并行** — 单文件 zstd 再加 -T 在 T=32 之后无效。

## Caveats
- text.bin 是 /usr 文本拼接 + 自重复，两机 seed 大小不同（93 MB vs 459 MB），所以 ratio 数字不直接可比（2.53 vs 4.55）。下次改用固定语料（silesia）。
- L22 在 9965 上的 ratio=29.75 是 seed 重复 artifact；6979P 上 ratio=5.31 才是更真实的数字。

完整对比报告：`comparison/2026-04-27-zstd-cross-server/README.md`

---

# 2026-04-27 — ZSTD 压缩 / 解压性能基准测试

## 背景与目标
评估 zstd（重点是 level 19）在一台高核数服务器上的压缩 / 解压性能：
- 解压速度上限是多少？
- L19 压缩多线程能 scale 到多少核？
- 不同数据特性（不可压缩 / 文本 / 全零）下结果差异多大？
- L19 相对其他 level 的成本/收益位置？

## 测试环境
- 远程主机：9965 (10.239.23.105)
- CPU：AMD EPYC 9965, 192 核 / 384 逻辑线程
- 内存：754 GiB
- 内核：Linux 6.8.0-88-generic (Ubuntu)
- zstd 版本：v1.5.5
- 工作目录：/mnt/oldhome/bjiang7/zstd-bench/

## 数据集 (各 1 GiB)
- random.bin — /dev/urandom，几乎不可压缩，用来测压缩器固定开销 + 解压内存带宽
- text.bin   — /usr/share/doc + /usr/include + /usr/share/man + /usr/share/locale 拼接（seed ≈ 93 MB，重复填到 1 GiB）。注意：文件内有自重复，对 long-range 模式（L22 --ultra）不公平
- zeros.bin  — /dev/zero，极端可压缩，用来看上界

## 方法
全部用 `zstd -bN -Tt -i10 <file>`：
- `-bN`：在 level N 下做压缩 + 解压基准
- `-Tt`：worker 线程数（0 = 自动用所有核）
- `-i10`：每次至少跑 10 秒，取最稳定的一次输出

测试 matrix：
1. L19, T=1 — 三个数据集基线
2. L19, T ∈ {2, 4, 8, 16, 32, 64, 0} — 三个数据集多线程 scaling
3. text.bin, T=1, level ∈ {1, 3, 9, 19, 22(--ultra)} — level 横向对比

驱动脚本：`scripts/run_bench.sh`，结果解析在 `scripts/plot.py`。
原始日志保留在 `raw/`，汇总在 `results.csv`。

## 关键结果

### L19 单线程基线
- random.bin： ratio 1.000，压缩 4.72 MB/s，解压 24,377 MB/s
- text.bin：   ratio 2.530，压缩 5.19 MB/s，解压 1,951 MB/s
- zeros.bin：  ratio 32,750，压缩 6,705 MB/s，解压 51,835 MB/s

### L19 压缩多线程 scaling（text.bin）
- T=1  →  5.2 MB/s
- T=2  →  9.5 MB/s
- T=4  →  18.3 MB/s
- T=8  →  33.8 MB/s
- T=16 →  59.8 MB/s
- T=32 → 103.3 MB/s
- T=64 → 103.0 MB/s （已饱和）
- T=0 (=384) → 103.0 MB/s

T=1→T=32 接近线性（约 20×），之后 zstd worker 池内部切片粒度成为瓶颈，给更多核也没收益。
**解压速度与线程数无关**，恒定约 1.95 GB/s — zstd 解压本质单线程。

### Level 横向对比 (text.bin, T=1)
| level | ratio  | compress MB/s | decompress MB/s |
|-------|--------|---------------|-----------------|
| 1     | 1.84   | 595           | 2208            |
| 3     | 2.04   | 441           | 2357            |
| 9     | 2.18   | 123           | 2637            |
| 19    | 2.53   | 5.2           | 1951            |
| 22*   | 29.75  | 44.5          | 11137           |

*L22 启用 `--ultra` + 默认更大窗口，配合 text.bin 的自重复结构，把整段重复识别成长距离引用，因此 ratio 跳到 29.75x、解压速度也异常高（大段是引用 copy 而非 entropy 解码）。这是数据集偏置，不是 L22 在真实文本上的表现。

## 主要观察 / 结论
1. zstd 解压速度与压缩 level 几乎解耦：L1~L22 在文本上都在 2~3 GB/s 量级；不可压数据接近 24 GB/s（基本是内存带宽上界）。
2. L19 压缩很贵：单线程 ~5 MB/s，比默认 L3 慢约 85×，换来 ratio 从 2.04 提到 2.53（+24%）。
3. 多线程压缩在这台机器上 ~32 worker 饱和，T>32 给更多核 = 浪费。如果想充分利用 384 线程，应该并行处理多个独立文件，而不是单文件加更多 -T。
4. 对"L19 解压有多快"的一个干净答案：**~2 GB/s/核（典型文本）**，几乎 = memcpy 上限的 ~1/12。
5. L22 的极端 ratio 是测试数据 artifact；要真正评估 long-range 模式，得换无自重复的数据集（如 silesia 拼接 + 打乱）。

## 留待后续
- 生成无自重复的 1 GiB 文本，重跑 L19 / L22，看 long-range 在真实文本上的真实增益
- 加 `--long=27` / dictionary mode 扫描
- 单 socket vs 跨 socket（NUMA）对压缩多线程的影响

## 文件位置
- 实验脚本与产物：`experiments/2026-04-27-zstd-benchmark/`
  - `results.csv` — 汇总
  - `system.txt`  — 环境快照
  - `plots/`      — 三张图（多线程 scaling / level 扫描 / 解压对比）
  - `raw/`        — 28 个 zstd -b 原始输出
  - `scripts/run_bench.sh` — 驱动脚本
  - `scripts/plot.py` — 解析 + 画图
