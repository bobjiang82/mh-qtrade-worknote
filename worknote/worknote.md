# Worknote

> 工作笔记：每次实验/排查/学习的简要记录。最新条目在最上方。

---

# 2026-04-27 — ZSTD 跨服务器对比 (srv-23-105 vs srv-23-11)

在第二台服务器 `10.239.23.11` 上跑了和昨天 (105) 同一套 zstd matrix，做横向对比。

## 第二台机环境
- CPU: Intel Xeon 6979P, 480 逻辑线程
- 内存: 754 GiB（测试时 ~408 GiB 已被其他进程占用，剩余 346 GiB 可用）
- zstd: v1.5.1（注意：105 上是 1.5.5，1.5.x 之间解压器无重大变更，认为可比）
- 工作目录: `/home/bjiang7/zstd-bench/`

## 主要差异（节选，详见 comparison/2026-04-27-zstd-cross-server/）

L19 单线程基线：

| 指标                | srv-23-105 (EPYC 9965) | srv-23-11 (Xeon 6979P) |
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
- L22 在 srv-23-105 上的 ratio=29.75 是 seed 重复 artifact；srv-23-11 上 ratio=5.31 才是更真实的数字。

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
- 远程主机：10.239.23.105
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
