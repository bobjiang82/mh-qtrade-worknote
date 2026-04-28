#!/usr/bin/env python3
"""
Pipeline decompression benchmark for quant blocks (batched).

Frames batched into BATCH-sized lists to amortize queue/lock overhead.
Reads N input files (each: sequence of [u32 LE length][zstd frame]),
runs F prefetch threads + D decompressor threads + 1 merger,
reports throughput, per-stage CPU, p50/p99 per-frame decompress latency.
"""
import argparse, os, sys, time, threading, queue, struct, statistics, csv, json
import zstandard as zstd

HEADER = struct.Struct("<I")
SENTINEL = None
BATCH = 256

def prefetcher(paths, frame_q, stop):
    for path in paths:
        if stop.is_set():
            break
        fd = os.open(path, os.O_RDONLY)
        try:
            try:
                os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_SEQUENTIAL)
            except Exception:
                pass
            buf = b""
            BUFSZ = 4 * 1024 * 1024
            batch = []
            while not stop.is_set():
                data = os.read(fd, BUFSZ)
                if not data:
                    break
                buf = buf + data if buf else data
                off = 0
                blen = len(buf)
                while blen - off >= 4:
                    (flen,) = HEADER.unpack_from(buf, off)
                    if blen - off - 4 < flen:
                        break
                    batch.append(bytes(buf[off+4 : off+4+flen]))
                    off += 4 + flen
                    if len(batch) >= BATCH:
                        frame_q.put(batch)
                        batch = []
                buf = buf[off:]
            if batch:
                frame_q.put(batch)
        finally:
            os.close(fd)

def decompressor(frame_q, out_q, ddict, lat_holder, stop, do_parse):
    dctx = zstd.ZstdDecompressor(dict_data=ddict) if ddict else zstd.ZstdDecompressor()
    decompress = dctx.decompress
    n = 0
    sum_t = 0.0
    samples = []
    samp_every = 31
    while not stop.is_set():
        try:
            batch = frame_q.get(timeout=0.5)
        except queue.Empty:
            continue
        if batch is SENTINEL:
            frame_q.put(SENTINEL)
            break
        t0 = time.perf_counter()
        if do_parse:
            outs = [decompress(f) for f in batch]
        else:
            for f in batch:
                decompress(f)
            outs = None
        t1 = time.perf_counter()
        bn = len(batch)
        n += bn
        sum_t += (t1 - t0)
        # sample some per-frame latency: total/bn average
        samples.append((t1 - t0) / bn)
        if do_parse and outs:
            out_q.put(outs)
    lat_holder.append((n, sum_t, samples))

def merger(out_q, stop, row_bytes, total_rows_holder):
    total = 0
    while not stop.is_set():
        try:
            batch = out_q.get(timeout=0.5)
        except queue.Empty:
            continue
        if batch is SENTINEL:
            break
        for raw in batch:
            n = len(raw) // row_bytes
            if n > 0:
                _ = raw[0:1]
            total += n
    total_rows_holder[0] = total

def run_once(args, files, ddict):
    stop = threading.Event()
    frame_q = queue.Queue(maxsize=args.queue)
    out_q = queue.Queue(maxsize=args.queue) if not args.no_merge else None
    lat_holder = []
    total_rows = [0]

    F = args.prefetch
    D = args.decomp
    buckets = [[] for _ in range(F)]
    for i, p in enumerate(files):
        buckets[i % F].append(p)

    pref_threads = [threading.Thread(target=prefetcher,
                                     args=(buckets[i], frame_q, stop),
                                     name=f"pf{i}", daemon=True)
                    for i in range(F)]
    decomp_threads = [threading.Thread(target=decompressor,
                                       args=(frame_q, out_q, ddict, lat_holder, stop, not args.no_merge),
                                       name=f"dc{i}", daemon=True)
                      for i in range(D)]
    merge_thread = None
    if not args.no_merge:
        merge_thread = threading.Thread(target=merger,
                                        args=(out_q, stop, args.row_bytes, total_rows),
                                        name="mg", daemon=True)

    t_start = time.perf_counter()
    for t in pref_threads: t.start()
    for t in decomp_threads: t.start()
    if merge_thread: merge_thread.start()

    for t in pref_threads: t.join()
    for _ in range(D):
        frame_q.put(SENTINEL)
    for t in decomp_threads: t.join()
    if merge_thread:
        out_q.put(SENTINEL)
        merge_thread.join()
    t_end = time.perf_counter()
    wall = t_end - t_start

    n_frames = sum(x[0] for x in lat_holder)
    avg_per_frame = []
    for _, _, samples in lat_holder:
        avg_per_frame.extend(samples)
    avg_per_frame.sort()

    raw_bytes = n_frames * args.rows_per_block * args.row_bytes
    comp_bytes = sum(os.path.getsize(p) for p in files)

    def pct(p):
        if not avg_per_frame: return 0.0
        k = min(int(len(avg_per_frame) * p), len(avg_per_frame)-1)
        return avg_per_frame[k]

    return {
        "wall_s": wall,
        "n_frames": n_frames,
        "raw_GB": raw_bytes / 1e9,
        "comp_GB": comp_bytes / 1e9,
        "raw_MBps": raw_bytes / 1e6 / wall if wall > 0 else 0,
        "comp_MBps": comp_bytes / 1e6 / wall if wall > 0 else 0,
        "ratio": raw_bytes / comp_bytes if comp_bytes else 0,
        "p50_us": pct(0.50) * 1e6,
        "p99_us": pct(0.99) * 1e6,
        "p999_us": pct(0.999) * 1e6,
        "rows_merged": total_rows[0],
    }

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--files", nargs="+", required=True)
    ap.add_argument("-F", "--prefetch", type=int, default=3)
    ap.add_argument("-D", "--decomp", type=int, default=6)
    ap.add_argument("--dict", default=None)
    ap.add_argument("--queue", type=int, default=64)
    ap.add_argument("--warmup", type=int, default=1)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--no-merge", action="store_true")
    ap.add_argument("--row-bytes", type=int, default=203)
    ap.add_argument("--rows-per-block", type=int, default=5)
    ap.add_argument("--out-csv", default=None)
    ap.add_argument("--tag", default="cell")
    args = ap.parse_args()

    ddict = None
    if args.dict:
        with open(args.dict, "rb") as f:
            ddict = zstd.ZstdCompressionDict(f.read())

    for _ in range(args.warmup):
        run_once(args, args.files, ddict)

    runs = []
    for i in range(args.runs):
        r = run_once(args, args.files, ddict)
        runs.append(r)
        print(f"[{args.tag}] run{i}: wall={r['wall_s']:.2f}s raw={r['raw_MBps']:.0f}MB/s comp={r['comp_MBps']:.0f}MB/s ratio={r['ratio']:.2f} p50={r['p50_us']:.1f}us p99={r['p99_us']:.1f}us frames={r['n_frames']}")

    def med(k): return statistics.median(r[k] for r in runs)
    summary = {
        "tag": args.tag,
        "F": args.prefetch,
        "D": args.decomp,
        "dict": "yes" if args.dict else "no",
        "merge": "no" if args.no_merge else "yes",
        "n_files": len(args.files),
        "raw_MBps_med": med("raw_MBps"),
        "comp_MBps_med": med("comp_MBps"),
        "ratio": runs[0]["ratio"],
        "p50_us_med": med("p50_us"),
        "p99_us_med": med("p99_us"),
        "p999_us_med": med("p999_us"),
        "wall_s_med": med("wall_s"),
        "n_frames": runs[0]["n_frames"],
    }
    print("SUMMARY", json.dumps(summary))
    if args.out_csv:
        new = not os.path.exists(args.out_csv)
        with open(args.out_csv, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(summary.keys()))
            if new: w.writeheader()
            w.writerow(summary)

if __name__ == "__main__":
    main()
