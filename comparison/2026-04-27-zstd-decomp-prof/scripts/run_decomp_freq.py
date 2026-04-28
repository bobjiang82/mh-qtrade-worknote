#!/usr/bin/env python3
"""Decompress latency + CPU freq profiling. Single-thread, NUMA/CPU pinned."""
import os, sys, time, csv, subprocess, threading, statistics, json, pathlib

if os.path.isdir("/mnt/oldhome/bjiang7"):
    BASE = "/mnt/oldhome/bjiang7"
else:
    BASE = "/home/bjiang7"

ZSTD = f"{BASE}/zstd157"
REFDIR = f"{BASE}/ref-frames"
OUTDIR = sys.argv[1]
N = 20
PIN_CPU = 0           # bind to CPU 0
NUMA_NODE = 0         # bind memory to node 0
SAMPLE_DT = 0.005     # 5 ms

os.makedirs(OUTDIR, exist_ok=True)
HOST = os.uname().nodename

def freq_path(cpu):
    return f"/sys/devices/system/cpu/cpu{cpu}/cpufreq/scaling_cur_freq"

def read_freq_khz():
    try:
        return int(open(freq_path(PIN_CPU)).read().strip())
    except Exception:
        return -1

def sysinfo():
    info = {"host": HOST}
    info["zstd_version"] = subprocess.check_output([ZSTD, "--version"]).decode().strip()
    try:
        info["governor"] = open(f"/sys/devices/system/cpu/cpu{PIN_CPU}/cpufreq/scaling_governor").read().strip()
        info["driver"]   = open(f"/sys/devices/system/cpu/cpu{PIN_CPU}/cpufreq/scaling_driver").read().strip()
        info["max_freq_khz"] = int(open(f"/sys/devices/system/cpu/cpu{PIN_CPU}/cpufreq/cpuinfo_max_freq").read().strip())
        info["min_freq_khz"] = int(open(f"/sys/devices/system/cpu/cpu{PIN_CPU}/cpufreq/cpuinfo_min_freq").read().strip())
    except Exception as e:
        info["err"] = str(e)
    info["cpu_model"] = subprocess.check_output(
        "grep -m1 'model name' /proc/cpuinfo", shell=True).decode().split(":",1)[1].strip()
    info["pin_cpu"] = PIN_CPU
    info["numa_node"] = NUMA_NODE
    info["N"] = N
    return info

def precheck_inputs():
    files = sorted(pathlib.Path(REFDIR).glob("silesia.l*.zst"))
    out = subprocess.check_output(["sha256sum", ZSTD] + [str(f) for f in files]).decode()
    return out.strip().splitlines()

def warm_cache():
    for f in pathlib.Path(REFDIR).glob("silesia.l*.zst"):
        subprocess.run(["dd", f"if={f}", "of=/dev/null", "bs=1M", "status=none"])

class FreqSampler(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.samples = []
        self._stopflag = threading.Event()
    def run(self):
        while not self._stopflag.is_set():
            f = read_freq_khz()
            if f > 0:
                self.samples.append(f)
            time.sleep(SAMPLE_DT)
    def stop(self):
        self._stopflag.set(); self.join(timeout=1)

def pct(xs, p):
    if not xs: return -1
    s = sorted(xs); k = (len(s)-1)*p; lo = int(k); hi = min(lo+1, len(s)-1)
    return s[lo] + (s[hi]-s[lo])*(k-lo)

def run_iter(level):
    ref = f"{REFDIR}/silesia.l{level}.zst"
    cmd = ["numactl", f"--cpunodebind={NUMA_NODE}", f"--membind={NUMA_NODE}",
           "taskset", "-c", str(PIN_CPU),
           ZSTD, "-d", "-T1", "-q", "-c", ref]
    sampler = FreqSampler()
    sampler.start()
    t0 = time.perf_counter()
    p = subprocess.run(cmd, stdout=subprocess.DEVNULL)
    sec = time.perf_counter() - t0
    sampler.stop()
    assert p.returncode == 0
    s = sampler.samples
    return {
        "seconds": sec,
        "freq_n": len(s),
        "freq_min_mhz": (min(s)/1000.0) if s else -1,
        "freq_p50_mhz": (pct(s,0.5)/1000.0) if s else -1,
        "freq_p90_mhz": (pct(s,0.9)/1000.0) if s else -1,
        "freq_max_mhz": (max(s)/1000.0) if s else -1,
        "freq_mean_mhz": (statistics.mean(s)/1000.0) if s else -1,
    }

def main():
    info = sysinfo()
    info["sha256"] = precheck_inputs()
    open(f"{OUTDIR}/info.json","w").write(json.dumps(info, indent=2))
    print(json.dumps(info, indent=2), flush=True)
    warm_cache()

    csv_path = f"{OUTDIR}/decomp_freq.csv"
    fields = ["host","op","level","threads","pin_cpu","numa","iter",
              "seconds","freq_n","freq_min_mhz","freq_p50_mhz","freq_p90_mhz","freq_max_mhz","freq_mean_mhz"]
    with open(csv_path,"w",newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader()
        for L in [1,9,19,22]:
            print(f"[decompress L={L}] ...", flush=True)
            for i in range(1, N+1):
                r = run_iter(L)
                row = {"host":HOST,"op":"decompress","level":L,"threads":1,
                       "pin_cpu":PIN_CPU,"numa":NUMA_NODE,"iter":i, **r}
                w.writerow(row); f.flush()
                if i==1 or i==N:
                    print(f"  L{L} iter{i}: {r['seconds']*1000:.1f} ms  freq p50={r['freq_p50_mhz']:.0f} MHz max={r['freq_max_mhz']:.0f} MHz", flush=True)
    print(f"DONE -> {csv_path}")

if __name__ == "__main__":
    main()
