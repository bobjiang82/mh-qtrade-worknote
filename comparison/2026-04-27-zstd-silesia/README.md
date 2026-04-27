# ZSTD 跨服务器对比 — silesia 固定语料 (2026-04-27)

使用业界标准 silesia 语料（打乱后做成单一 tar），两机使用同一份 sha256 校验过的输入文件，结果完全可比。

## 测试对象

| 标签   | 主机          | CPU              | 逻辑核 | zstd   |
|--------|---------------|------------------|--------|--------|
| 9965   | 10.239.23.105 | AMD EPYC 9965    | 384    | v1.5.5 |
| 6979P  | 10.239.23.11  | Intel Xeon 6979P | 480    | v1.5.1 |

## 语料

- 原始：`silesia.zip` 来自 https://sun.aei.polsl.pl/~sdeor/corpus/silesia.zip
- 解压得到 12 个文件 (dickens / mozilla / mr / nci / ooffice / osdb / reymont / samba / sao / webster / xml / x-ray)。
- 用固定随机种子 `random.seed(42)` 打乱顺序（`tar --no-recursion -T file_list`）打包成 `silesia_shuf.tar`。
- 大小 211,957,760 B (≈ 211 MB)。
- **SHA256: `ebfcba3cc035681740f052be20226d751d10082beddbc8b4c9c1c2e02d334400`** — 两机相同。

## Matrix

`zstd -b -i10` 每点最少 10 秒：
- L19 T=1 单线程基线
- L19 T ∈ {2, 4, 8, 16, 32, 64, 0(=all)} 多线程 scaling
- T=1 上 level ∈ {1, 3, 9, 19, 22(--ultra)} 横扫

> 注意：silesia_shuf.tar 只有 211 MB。L19 默认 8MB 窗口 → 仅 ~26 个独立窗口，对单文件多线程压缩 zstd 给到大约 T=8 就饱和（下面数据可见）。这是数据量限制，不是硬件。

## 关键数字 (T=1)

| level | ratio (两机一致) | 9965 C MB/s | 6979P C MB/s | 9965 D MB/s | 6979P D MB/s |
|-------|------------------|-------------|---------------|-------------|----------------|
| 1     | 2.887            |  472.9      |  543.2        | 1634        | 1466           |
| 3     | 3.186            |  301.2      |  354.7        | 1471        | 1455           |
| 9     | 3.574 / 3.570    |   74.4      |   78.0        | 1582        | 1488           |
| 19    | 3.999            |    3.58     |    4.33       | 1362        | 1260           |
| 22    | 4.042 / 4.041    |    2.53     |    3.12       | 1283        | 1209           |

（"两机一致"列含义：同一份输入两机的 zstd 输出 size 几乎一致，差别 < 1e-3，证明 1.5.5 与 1.5.1 在压缩 ratio 上等价。）

观察：
- **Ratio 两机几乎完全一致** (4.042 vs 4.041 @ L22)。固定语料 + 同语料校验做到的可比性。
- **低 level (L1~L9) 压缩 6979P 单核快 5~15%**：在压缩吞吐主导的低 level 阶段（短匹配 + entropy），Xeon 6979P 单核 IPC/频率占优。
- **高 level (L19/L22) 压缩 6979P 仍快 ~20%**：4.33 vs 3.58 MB/s @ L19，3.12 vs 2.53 @ L22。
- **解压速度 9965 领先 5~10%**：1362 vs 1260 @ L19。差距比同语料压缩侧的差距小。

## L19 多线程 scaling (silesia_shuf.tar, 211 MB)

| threads | 9965 MB/s | 6979P MB/s |
|---------|-----------|-------------|
| 1       |   3.58    |   4.33      |
| 2       |   6.25    |   6.48      |
| 4       |  10.40    |  11.40      |
| 8       |  15.30    |  14.90      |
| 16      |  15.50    |  13.80      |
| 32      |  15.50    |  13.20      |
| 64      |  15.70    |  14.10      |
| 0 (all) |  15.50    |  13.20      |

- **T=8 即饱和** (~15 MB/s)。原因：silesia_shuf.tar 211 MB 下 8MB 窗口 ≈ 26 块，单文件 worker 拿不到更多任务。再加线程没收益，跟硬件无关。
- 6979P 在 T≥8 后的小幅退化（13~14 MB/s）比 9965 的稳定 ~15.5 略差，可能是 Xeon 跨 NUMA 调度成本，但 silesia 数据量本身太小，结论强度不高。

## 结论

1. **Ratio 两机一致 (~4.04 @ L22)**：固定语料保证了真正可比性。
2. **L19/L22 单核压缩 6979P 快 ~20%**（真实文本数据上）。
3. **解压速度 9965 略快 ~8%**（L19）。
4. **多线程压缩饱和点完全取决于输入大小**：211 MB 在 T=8 饱和。要充分用 100+ 核必须并行多文件。

## Caveats

- silesia_shuf.tar 只有 211 MB，多线程 scaling 数据点稀疏；想看高线程上限需要更大语料 (e.g. 多份 silesia 拼接到 4~8 GiB)。
- zstd 版本不同 (1.5.5 vs 1.5.1)。压缩 ratio 上面已经验证基本等价；但 1.5.5 多线程切片有微优化，对大数据多线程对比仍是噪声源。
- 6979P 测试时仍有其他负载占用 ~408 GiB 内存，CPU 噪声偏向使 6979P 略低估。

## 文件

- `silesia_shuf.tar` 没入库（211 MB），可由 `scripts/run_bench_silesia.sh` 重新构造。
- `scripts/run_bench_silesia.sh` — 跑测脚本（两机用同一份）
- `scripts/make_plots.py` — 出图脚本
- `results.csv` — 24 个数据点合并表
- `raw-9965/`, `raw-6979P/` — 原始 zstd -b 输出
- `plots/cmp_l19_singlethread.png` — L19 T=1 压缩/解压并排
- `plots/cmp_l19_threads.png` — L19 多线程 scaling 双线
- `plots/cmp_level_sweep.png` — level vs compress / decompress / ratio 三联图

## 下次再做

- 拼 4~8 GiB 大 silesia 让多线程 scaling 真正展开
- 锁同一 zstd binary (1.5.5 编出二进制部署到两机)
- `--long=27` / dictionary mode
- 单 socket / NUMA-bind 对比
