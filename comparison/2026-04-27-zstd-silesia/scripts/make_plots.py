
import csv
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

rows = list(csv.DictReader(open("/tmp/silesia-compare/results.csv")))
for r in rows:
    r['level']=int(r['level']); r['threads']=int(r['threads'])
    r['ratio']=float(r['ratio']); r['comp_MBs']=float(r['comp_MBs']); r['decomp_MBs']=float(r['decomp_MBs'])

SERVERS = {"9965":"EPYC 9965 (384T)", "6979P":"Xeon 6979P (480T)"}
COLORS = {"9965":"#d62728", "6979P":"#1f77b4"}
outdir = Path("/tmp/silesia-compare/plots"); outdir.mkdir(exist_ok=True)

fig, ax = plt.subplots(figsize=(8,5))
for srv,label in SERVERS.items():
    pts = sorted([(r['threads'], r['comp_MBs']) for r in rows if r['server']==srv and r['level']==19 and r['threads']!=0])
    xs,ys = zip(*pts)
    ax.plot(xs, ys, "o-", color=COLORS[srv], label=label, linewidth=2, markersize=8)
ax.set_xscale("log", base=2)
ax.set_xticks([1,2,4,8,16,32,64]); ax.set_xticklabels([1,2,4,8,16,32,64])
ax.set_xlabel("threads (-T)"); ax.set_ylabel("compress MB/s")
ax.set_title("zstd -19 thread scaling on silesia_shuf.tar (211 MB)")
ax.grid(alpha=0.3); ax.legend()
fig.tight_layout(); fig.savefig(outdir/"cmp_l19_threads.png", dpi=120); plt.close()

levels = [1,3,9,19,22]
fig, axes = plt.subplots(1,3, figsize=(15,4.5))
for ax, key, title, ylog in [
    (axes[0], 'comp_MBs',   'compress MB/s (T=1)', True),
    (axes[1], 'decomp_MBs', 'decompress MB/s (T=1)', False),
    (axes[2], 'ratio',      'compression ratio', False),
]:
    for srv,label in SERVERS.items():
        pts = sorted([(r['level'], r[key]) for r in rows if r['server']==srv and r['threads']==1 and r['level'] in levels])
        xs,ys = zip(*pts)
        ax.plot(xs, ys, "o-", color=COLORS[srv], label=label, linewidth=2, markersize=8)
    if ylog: ax.set_yscale("log")
    ax.set_xticks(levels); ax.set_xlabel("zstd level"); ax.set_title(title)
    ax.grid(alpha=0.3); ax.legend()
fig.suptitle("zstd level sweep on silesia_shuf.tar (T=1)")
fig.tight_layout(); fig.savefig(outdir/"cmp_level_sweep.png", dpi=120); plt.close()

fig, axes = plt.subplots(1,2, figsize=(10,4.5))
labels = list(SERVERS.values())
comp = [next(r['comp_MBs'] for r in rows if r['server']==s and r['level']==19 and r['threads']==1) for s in SERVERS]
dec  = [next(r['decomp_MBs'] for r in rows if r['server']==s and r['level']==19 and r['threads']==1) for s in SERVERS]
axes[0].bar(labels, comp, color=[COLORS[s] for s in SERVERS]); axes[0].set_ylabel("MB/s"); axes[0].set_title("L19 T=1 compress")
for i,v in enumerate(comp): axes[0].text(i,v,f"{v:.2f}",ha='center',va='bottom')
axes[1].bar(labels, dec, color=[COLORS[s] for s in SERVERS]); axes[1].set_ylabel("MB/s"); axes[1].set_title("L19 T=1 decompress")
for i,v in enumerate(dec): axes[1].text(i,v,f"{v:.0f}",ha='center',va='bottom')
fig.suptitle("zstd L19 single-thread on silesia_shuf.tar")
fig.tight_layout(); fig.savefig(outdir/"cmp_l19_singlethread.png", dpi=120); plt.close()
print(sorted(p.name for p in outdir.iterdir()))
