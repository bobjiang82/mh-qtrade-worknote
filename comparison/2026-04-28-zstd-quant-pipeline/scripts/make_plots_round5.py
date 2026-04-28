#!/usr/bin/env python3
"""Round-5 plots: throughput by F/D, dict effect, host comparison, latency."""
import csv, os
import matplotlib.pyplot as plt
import numpy as np

OUT = "/tmp/round5-plots"
os.makedirs(OUT, exist_ok=True)

def load(path, host):
    rows = []
    with open(path) as f:
        for r in csv.DictReader(f):
            r["host"] = host
            for k in ("F","D","n_files","n_frames"): r[k] = int(r[k])
            for k in ("raw_MBps_med","comp_MBps_med","ratio","p50_us_med","p99_us_med","p999_us_med","wall_s_med"):
                r[k] = float(r[k])
            # average duplicates by tag
            rows.append(r)
    return rows

def med_by_tag(rows):
    by = {}
    for r in rows:
        by.setdefault((r["host"], r["tag"]), []).append(r)
    out = []
    for k, lst in by.items():
        avg = dict(lst[0])
        for f in ("raw_MBps_med","comp_MBps_med","p50_us_med","p99_us_med","wall_s_med"):
            avg[f] = float(np.median([x[f] for x in lst]))
        out.append(avg)
    return out

a = load("/tmp/quant-9965/results.csv", "9965")
b = load("/tmp/quant-6979P/results.csv", "6979P")
rows = med_by_tag(a + b)

def filt(host, dict_, pin, cache):
    return sorted([r for r in rows if r["host"]==host and r["dict"]==dict_ and r["tag"].endswith(f"_pin{pin}_{cache}")],
                  key=lambda r: (r["F"], r["D"]))

# Plot 1: throughput vs F (warm, default pin), dict yes/no, both hosts
fig, ax = plt.subplots(figsize=(10,6))
configs = [(F, D) for F,D in [(1,2),(3,3),(3,6),(6,6),(6,12)]]
xs = [f"F{F}/D{D}" for F,D in configs]
x = np.arange(len(xs))
w = 0.2
for i,(host,dict_,color) in enumerate([("9965","no","#1f77b4"),("9965","yes","#ff7f0e"),("6979P","no","#2ca02c"),("6979P","yes","#d62728")]):
    pts = filt(host, dict_, "default", "warm")
    pmap = {(r["F"], r["D"]): r["raw_MBps_med"] for r in pts}
    y = [pmap.get(c, 0) for c in configs]
    ax.bar(x + (i-1.5)*w, y, w, label=f"{host} dict={dict_}", color=color)
ax.set_xticks(x); ax.set_xticklabels(xs)
ax.set_ylabel("Raw decompress throughput (MB/s)")
ax.set_title("Round-5 zstd quant decomp throughput (warm, default pin)")
ax.legend(); ax.grid(axis="y", alpha=0.3)
plt.tight_layout(); plt.savefig(f"{OUT}/throughput.png", dpi=110); plt.close()

# Plot 2: dict effect (compression ratio)
fig, ax = plt.subplots(figsize=(6,4))
labels = ["9965 no-dict","9965 dict","6979P no-dict","6979P dict"]
ratios = []
for host, d in [("9965","no"),("9965","yes"),("6979P","no"),("6979P","yes")]:
    rs = [r["ratio"] for r in rows if r["host"]==host and r["dict"]==d]
    ratios.append(float(np.mean(rs)))
ax.bar(labels, ratios, color=["#1f77b4","#ff7f0e","#2ca02c","#d62728"])
for i,v in enumerate(ratios):
    ax.text(i, v+0.02, f"{v:.2f}x", ha="center")
ax.set_ylabel("Compression ratio (raw/comp)")
ax.set_title("Dict (64KB, trained from 10k samples) effect on 1KB blocks")
ax.set_ylim(0, max(ratios)*1.2)
plt.tight_layout(); plt.savefig(f"{OUT}/dict_ratio.png", dpi=110); plt.close()

# Plot 3: latency p50 vs p99 (F=6, D=12 warm default)
fig, ax = plt.subplots(figsize=(8,5))
hosts_dicts = [("9965","no"),("9965","yes"),("6979P","no"),("6979P","yes")]
p50s, p99s, names = [], [], []
for host, d in hosts_dicts:
    rs = [r for r in rows if r["host"]==host and r["dict"]==d and r["F"]==6 and r["D"]==12 and r["tag"].endswith("_pindefault_warm")]
    if rs:
        p50s.append(rs[0]["p50_us_med"]); p99s.append(rs[0]["p99_us_med"])
        names.append(f"{host}\ndict={d}")
x = np.arange(len(names)); w=0.35
ax.bar(x-w/2, p50s, w, label="p50", color="#1f77b4")
ax.bar(x+w/2, p99s, w, label="p99", color="#ff7f0e")
ax.set_xticks(x); ax.set_xticklabels(names)
ax.set_ylabel("Per-frame decompress latency (µs)")
ax.set_title("Round-5 latency (F=6, D=12, warm)")
ax.legend(); ax.grid(axis="y", alpha=0.3)
plt.tight_layout(); plt.savefig(f"{OUT}/latency.png", dpi=110); plt.close()

# Plot 4: cold vs warm vs numa (6979P only, F=6 D=12)
fig, ax = plt.subplots(figsize=(8,5))
scenarios = [("warm","default"),("cold","default"),("warm","numa")]
labels = ["warm/default","cold/default","warm/numa"]
for j,d in enumerate(["no","yes"]):
    ys=[]
    for cache,pin in scenarios:
        rs=[r for r in rows if r["host"]=="6979P" and r["dict"]==d and r["F"]==6 and r["D"]==12 and r["tag"].endswith(f"_pin{pin}_{cache}")]
        ys.append(rs[0]["raw_MBps_med"] if rs else 0)
    x=np.arange(len(labels))+j*0.4
    ax.bar(x, ys, 0.4, label=f"dict={d}")
for i,(lbl,_) in enumerate(zip(labels,scenarios)):
    pass
ax.set_xticks(np.arange(len(labels))+0.2); ax.set_xticklabels(labels)
ax.set_ylabel("Raw throughput (MB/s)")
ax.set_title("6979P: cold vs warm vs numa (F=6, D=12)")
ax.legend(); ax.grid(axis="y", alpha=0.3)
plt.tight_layout(); plt.savefig(f"{OUT}/6979P_scenarios.png", dpi=110); plt.close()

print("Plots written to", OUT)
for f in os.listdir(OUT):
    print(" -", f, os.path.getsize(f"{OUT}/{f}"), "bytes")
