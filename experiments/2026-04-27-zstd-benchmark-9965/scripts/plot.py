import csv, matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

rows=list(csv.DictReader(open('/tmp/zstd-bench/results.csv')))
def f(x):
    try: return float(x)
    except: return None

fig, ax = plt.subplots(figsize=(8,5))
for ds in ['random.bin','text.bin','zeros.bin']:
    pts=[(int(r['threads']) or 384, f(r['c_speed_MBps']))
         for r in rows if r['dataset']==ds and r['level']=='19' and r['c_speed_MBps']]
    pts.sort()
    ax.plot([p[0] for p in pts],[p[1] for p in pts], marker='o', label=ds)
ax.set_xscale('log', base=2); ax.set_yscale('log')
ax.set_xlabel('threads (-T;  0 plotted as 384)')
ax.set_ylabel('compression speed MB/s (log)')
ax.set_title('zstd L19 compression scaling - AMD EPYC 9965 (1 GiB)')
ax.grid(True, which='both', alpha=0.3); ax.legend()
plt.tight_layout(); plt.savefig('/tmp/zstd-bench/plot_l19_threads.png', dpi=120); plt.close()

fig, ax1 = plt.subplots(figsize=(8,5))
sub=sorted([r for r in rows if r['dataset']=='text.bin' and r['threads']=='1'], key=lambda r:int(r['level']))
lvls=[int(r['level']) for r in sub]
ax1.plot(lvls,[f(r['c_speed_MBps']) for r in sub],'o-',color='tab:blue',label='compress MB/s')
ax1.plot(lvls,[f(r['d_speed_MBps']) for r in sub],'s-',color='tab:orange',label='decompress MB/s')
ax1.set_yscale('log'); ax1.set_xlabel('zstd level'); ax1.set_ylabel('speed MB/s (log)')
ax1.grid(True, alpha=0.3)
ax2=ax1.twinx()
ax2.plot(lvls,[f(r['ratio']) for r in sub],'^--',color='tab:green',label='ratio')
ax2.set_ylabel('compression ratio (x)')
ax1.set_title('zstd level sweep - text.bin, T=1')
l1,la1=ax1.get_legend_handles_labels(); l2,la2=ax2.get_legend_handles_labels()
ax1.legend(l1+l2,la1+la2,loc='center left')
plt.tight_layout(); plt.savefig('/tmp/zstd-bench/plot_level_sweep.png', dpi=120); plt.close()

fig, ax = plt.subplots(figsize=(8,4.5))
ds_names=['random.bin','text.bin','zeros.bin']
means=[sum(f(r['d_speed_MBps']) for r in rows if r['dataset']==ds and r['level']=='19' and r['d_speed_MBps'])
      / max(1,sum(1 for r in rows if r['dataset']==ds and r['level']=='19' and r['d_speed_MBps']))
      for ds in ds_names]
bars=ax.bar(ds_names, means, color=['tab:red','tab:blue','tab:green'])
for b,v in zip(bars,means):
    ax.text(b.get_x()+b.get_width()/2, v, f'{v:,.0f}', ha='center', va='bottom')
ax.set_ylabel('decompress MB/s (avg over thread runs)')
ax.set_title('zstd L19 decompression speed by dataset')
ax.grid(True, axis='y', alpha=0.3)
plt.tight_layout(); plt.savefig('/tmp/zstd-bench/plot_decomp.png', dpi=120); plt.close()
print("OK")
