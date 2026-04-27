"""Plot zstd benchmark results comparing two servers side by side."""
import csv, matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

SERVERS = {
    'EPYC 9965 (384T)': '/tmp/zstd-bench/results.csv',
    'Xeon 6979P (480T)': '/tmp/zstd-bench-srv11/results.csv',
}
OUT = '/tmp/zstd-compare'
import os; os.makedirs(OUT, exist_ok=True)

def load(p):
    return list(csv.DictReader(open(p)))

def f(x):
    try: return float(x)
    except: return None

data = {name: load(p) for name, p in SERVERS.items()}

# --- Plot 1: L19 multi-thread compression scaling on text.bin (both servers) ---
fig, ax = plt.subplots(figsize=(9,5.5))
for name, rows in data.items():
    pts=[(int(r['threads']) or 999, f(r['c_speed_MBps']))
         for r in rows if r['dataset']=='text.bin' and r['level']=='19' and r['c_speed_MBps']]
    pts.sort()
    xs=[p[0] for p in pts]; ys=[p[1] for p in pts]
    ax.plot(xs, ys, marker='o', label=name)
ax.set_xscale('log', base=2); ax.set_yscale('log')
ax.set_xlabel('threads (-T;  0 plotted as 999 = "all cores")')
ax.set_ylabel('compress MB/s (log)')
ax.set_title('zstd L19 compression scaling on text.bin')
ax.grid(True, which='both', alpha=0.3); ax.legend()
plt.tight_layout(); plt.savefig(f'{OUT}/cmp_l19_threads.png', dpi=120); plt.close()

# --- Plot 2: L19 single-thread per dataset (compress + decompress) ---
fig, axes = plt.subplots(1,2, figsize=(12,4.5))
ds_names=['random.bin','text.bin','zeros.bin']
import numpy as np
x = np.arange(len(ds_names)); w=0.35
for i, (name, rows) in enumerate(data.items()):
    cs=[next((f(r['c_speed_MBps']) for r in rows if r['dataset']==d and r['level']=='19' and r['threads']=='1'), 0) for d in ds_names]
    ds=[next((f(r['d_speed_MBps']) for r in rows if r['dataset']==d and r['level']=='19' and r['threads']=='1'), 0) for d in ds_names]
    axes[0].bar(x + (i-0.5)*w, cs, w, label=name)
    axes[1].bar(x + (i-0.5)*w, ds, w, label=name)
for ax, title, ylab in [(axes[0],'L19 compress (T=1)','compress MB/s'),(axes[1],'L19 decompress (T=1)','decompress MB/s')]:
    ax.set_xticks(x); ax.set_xticklabels(ds_names)
    ax.set_yscale('log'); ax.set_ylabel(ylab); ax.set_title(title)
    ax.grid(True, axis='y', alpha=0.3); ax.legend(fontsize=8)
plt.tight_layout(); plt.savefig(f'{OUT}/cmp_l19_singlethread.png', dpi=120); plt.close()

# --- Plot 3: level sweep on text.bin (T=1) ratio + decompress ---
fig, (ax1, ax2) = plt.subplots(1,2, figsize=(12,4.5))
for name, rows in data.items():
    sub=sorted([r for r in rows if r['dataset']=='text.bin' and r['threads']=='1'], key=lambda r:int(r['level']))
    lvls=[int(r['level']) for r in sub]
    ax1.plot(lvls, [f(r['ratio']) for r in sub], 'o-', label=name)
    ax2.plot(lvls, [f(r['d_speed_MBps']) for r in sub], 'o-', label=name)
ax1.set_xlabel('level'); ax1.set_ylabel('compression ratio (x)'); ax1.set_title('text.bin ratio vs level')
ax1.grid(True, alpha=0.3); ax1.legend(fontsize=8)
ax2.set_xlabel('level'); ax2.set_ylabel('decompress MB/s'); ax2.set_title('text.bin decompress speed vs level')
ax2.grid(True, alpha=0.3); ax2.legend(fontsize=8)
plt.tight_layout(); plt.savefig(f'{OUT}/cmp_level_sweep.png', dpi=120); plt.close()

print("OK")
for p in os.listdir(OUT): print(p, os.path.getsize(f'{OUT}/{p}'))
