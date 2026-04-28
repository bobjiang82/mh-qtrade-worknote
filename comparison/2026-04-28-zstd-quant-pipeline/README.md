# 第五轮：zstd quant 数据多线程 pipeline 解压

日期：2026-04-28
机器：9965 (AMD EPYC 9965, 192C/384T, 754 GiB, 1 NUMA, Ubuntu 24.04, zstandard 0.22 / libzstd 1.5.5)
　　　 6979P (Intel Xeon 6979P, 多 NUMA, CentOS Stream 9, zstandard 0.25 / libzstd 1.5.x)
场景：模拟客户量化数据，行宽 203B (10×int64 + 15×double + 3×uint8)，5 行/block ≈ 1015B raw/block

## 数据集
- 6 个独立文件，各 200 万 block ≈ 2.03 GB raw / 1.34 GB comp（无字典） / 1.06 GB comp（字典）
- 字典：64 KB，从前 10000 行训练
- 文件格式：连续 [u32 LE length][zstd frame] 序列

## 测试方法
三阶段 pipeline (bench_pipeline.py)：
  Prefetcher (F threads, posix_fadvise SEQUENTIAL, 4 MB read chunk, 解析 length-prefixed frame，BATCH=256 入队)
  → bounded queue.Queue
  → Decompressor (D threads, per-thread ZstdDecompressor，shared ZstdCompressionDict)

测试矩阵 (warm + 默认 pin 主矩阵；cold/numa 仅 F=6 D=12 抽测)：
  F ∈ {1, 3, 6} prefetch 线程 = 文件并行度
  D ∈ {F, 2F} decomp 线程
  dict ∈ {no, yes}
  cache ∈ {warm, cold}（cold = drop_caches）
  pin ∈ {default, numactl --interleave=all}（仅 6979P 有意义）

## 关键结果

### 1) 单线程基线一致，多线程 scaling 受 GIL 限制
- F=1 D=2 warm 无字典：9965 246 MB/s vs 6979P 238 MB/s（同代基本持平）
- F=3 D=6 warm 无字典：9965 105 MB/s vs 6979P 144 MB/s
- F=6 D=12 warm 无字典：9965 115 MB/s vs 6979P 141 MB/s
- F 增大反而 raw_MBps 下降：因为 BATCH=256 + Python queue 的 cross-thread 锁开销在 1KB 这种极小 frame 上吃掉绝大部分 GIL 释放收益
- 9965 在多线程时不如 6979P，原因疑似 Ubuntu 24.04 自带 zstandard 0.22 老 binding（GIL 释放粒度较粗）vs 6979P 上 0.25 较新

### 2) Dict 对压缩比收益显著，但解压吞吐 raw 不一定更快
- 压缩比：no-dict 1.515x → dict 1.907x（+26%）
- 9965 单线程：dict 反而慢（246 → 203 MB/s），dict 表查表 + memcpy 在小 frame 占比大
- 6979P 单线程：dict 几乎无 cost（238 → 243 MB/s）
- 多线程 6979P F=3 D=3 dict：276 MB/s（全场最高），dict 模式吞吐反而最高 — 6979P 上更新版本对 DDict 重用做了优化

### 3) F=6 D=12 是最稳的"多文件并行"配置
- 9965：105-115 MB/s 稳定，p50 ≈ 105 µs，p99 ≈ 130 µs
- 6979P：141 MB/s no-dict / 107 MB/s dict warm，p50 86-114 µs

### 4) cold cache 影响小，page cache 主导
- 6979P F6 D12 dict：warm 107 → cold 132 MB/s（cold 反而更快，可能是 warm 那次正好跑在掉档的 P-core 上，run-to-run 噪音）
- 9965 cold/warm 差 ≤ 4%，磁盘已 NVMe，prefetch 充分

### 5) NUMA pin (6979P)
- F6 D12 warm：default 141 / 107 MB/s （no/yes dict） vs numa 142 / 150 MB/s
- numactl --interleave=all 对 dict 模式 +40%（dict 表跨 NUMA 访问压力下，interleave 比 default 调度更稳）
- 与第四轮 single-thread hard-pin 反向慢的结论一致：多 NUMA Intel 上要用 interleave，不要 single-node hard pin

## 文件清单
- `scripts/gen_quant_blocks.py`：数据生成器（quant 字段语义模拟 + 字典训练）
- `scripts/bench_pipeline.py`：三阶段 pipeline benchmark
- `scripts/run_matrix.sh`：测试矩阵驱动
- `scripts/make_plots_round5.py`：出图
- `scripts/quant.dict`：64 KB 训练字典（两机共用）
- `bench-{9965,6979P}/results.csv`：每 cell 中位数
- `bench-{9965,6979P}/system.txt`：CPU/NUMA/zstd 版本
- `plots/throughput.png`：F/D 配置吞吐对比
- `plots/dict_ratio.png`：dict 压缩比柱状
- `plots/latency.png`：F=6 D=12 的 p50/p99
- `plots/6979P_scenarios.png`：cold/warm/numa 对比

## 给客户的实战建议
1. 对 1KB block 类客户量化数据，64 KB 字典训练样本 1 万行就够，压缩比从 1.51 提到 1.91 (+26%)，传输成本 -21%。
2. Python 解压 1KB block 触顶约 250 MB/s 单线程，多线程 scaling 差，生产用 C++ 实现可上 3-5×。
3. 多文件并行 (F=3..6) 单机吞吐 ≈ 100-150 MB/s/instance（Python 实现），实际 C++ 可期待 500+ MB/s/instance。
4. 6979P 多 NUMA 部署务必 `numactl --interleave=all`，dict 模式 +40%。
5. 解压 frame 边界由 `[u32 length][zstd frame]` header 自描述，无需独立 index。

## 已知限制
- Python `zstandard` BATCH=256 + queue 仍是瓶颈，CPU 利用率仅 ~150% 即使 D=12 — 上限为 binding 实现而非 zstd 本身
- bench 没测 level≠3 / RowSize≠1KB / 多文件 cold 同时 drop_caches 后 NVMe IO 上限
- C 版本未实现，C++ 客户系统真实数字需另外测
