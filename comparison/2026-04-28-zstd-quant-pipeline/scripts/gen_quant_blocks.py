#!/usr/bin/env python3
"""Generate quant-like blocks: 10 int64 + 15 double + 3 uint8 = 203 B/row.
~5 rows / block ≈ 1015 B raw → zstd compress → write [u32 len][comp_bytes].
"""
import argparse, struct, time, os, numpy as np
import zstandard as zstd

ROW_BYTES = 10*8 + 15*8 + 3*1  # 203
ROWS_PER_BLOCK = 5             # 5 * 203 = 1015 ≈ 1KB

def make_chunk(n_rows, row0, rng):
    # 10 int64
    ts      = (np.arange(n_rows, dtype=np.int64) + row0) * 1000 \
              + rng.integers(0, 100, n_rows, dtype=np.int64)
    sym_id  = rng.integers(1, 5000, n_rows, dtype=np.int64)
    seq     = (np.arange(n_rows, dtype=np.int64) + row0)
    other_i = rng.integers(0, 1<<20, (n_rows, 7), dtype=np.int64)
    ints    = np.column_stack([ts, sym_id, seq, other_i])           # (n,10)

    # 15 double — price / qty / misc, rounded
    base    = 100.0 + (row0 % 4000) * 0.01
    px      = np.round(base + np.cumsum(rng.normal(0, 0.01, (n_rows, 5)), axis=0), 4)
    qty     = np.round(rng.lognormal(3, 1, (n_rows, 5)))
    misc    = np.round(rng.normal(0, 1, (n_rows, 5)), 4)
    dbls    = np.column_stack([px, qty, misc])                      # (n,15)

    # 3 uint8 — biased categorical
    u8 = rng.choice(np.arange(8, dtype=np.uint8), size=(n_rows, 3),
                    p=[0.4,0.2,0.15,0.1,0.05,0.04,0.04,0.02])

    # interleave to row-major bytes
    buf = np.empty(n_rows*ROW_BYTES, dtype=np.uint8)
    for i in range(n_rows):
        off = i*ROW_BYTES
        buf[off:off+80]      = np.frombuffer(ints[i].tobytes(), dtype=np.uint8)
        buf[off+80:off+200]  = np.frombuffer(dbls[i].tobytes(), dtype=np.uint8)
        buf[off+200:off+203] = u8[i]
    return buf.tobytes()

def gen_file(path, n_blocks, level, dict_path, seed):
    rng = np.random.default_rng(seed)
    if dict_path:
        ddata = zstd.ZstdCompressionDict(open(dict_path,'rb').read())
        cctx = zstd.ZstdCompressor(level=level, dict_data=ddata)
    else:
        cctx = zstd.ZstdCompressor(level=level)
    raw_total = comp_total = 0
    t0 = time.time()
    with open(path, 'wb') as f:
        # batch generation for speed (generate 10K rows at a time, then split)
        BATCH_BLOCKS = 2000
        for b0 in range(0, n_blocks, BATCH_BLOCKS):
            n_b = min(BATCH_BLOCKS, n_blocks - b0)
            big = make_chunk(n_b * ROWS_PER_BLOCK, b0*ROWS_PER_BLOCK, rng)
            block_size = ROWS_PER_BLOCK * ROW_BYTES
            for i in range(n_b):
                raw = big[i*block_size:(i+1)*block_size]
                comp = cctx.compress(raw)
                f.write(struct.pack('<I', len(comp)))
                f.write(comp)
                raw_total += len(raw); comp_total += len(comp)
            if b0 % 200000 == 0 and b0 > 0:
                el = time.time()-t0
                print(f'  ... {b0}/{n_blocks} blocks, {el:.1f}s, '
                      f'{raw_total/el/1e6:.1f} MB/s raw',
                      flush=True)
    dt = time.time()-t0
    print(f'{path}: {n_blocks} blocks  raw={raw_total/1e9:.2f}GB '
          f'comp={comp_total/1e9:.2f}GB  ratio={raw_total/comp_total:.2f}  '
          f'{dt:.1f}s ({raw_total/dt/1e6:.1f} MB/s raw)')
    return raw_total, comp_total

def train_dict(sample_blocks, dict_size, seed=0):
    rng = np.random.default_rng(seed)
    samples = []
    for i in range(sample_blocks):
        samples.append(make_chunk(ROWS_PER_BLOCK, i*ROWS_PER_BLOCK, rng))
    print(f'training on {len(samples)} samples, target dict_size={dict_size}')
    t0 = time.time()
    d = zstd.train_dictionary(dict_size, samples)
    print(f'dict trained in {time.time()-t0:.1f}s, actual size={len(d.as_bytes())} B')
    return d.as_bytes()

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', required=True)
    ap.add_argument('--n-blocks', type=int, default=2_000_000)
    ap.add_argument('--level', type=int, default=3)
    ap.add_argument('--dict', default='')
    ap.add_argument('--seed', type=int, default=42)
    ap.add_argument('--train-dict', action='store_true',
                    help='instead of generating data, train and save dict to --out')
    ap.add_argument('--dict-size', type=int, default=64*1024)
    ap.add_argument('--train-samples', type=int, default=10000)
    args = ap.parse_args()
    if args.train_dict:
        d = train_dict(args.train_samples, args.dict_size, seed=args.seed)
        open(args.out,'wb').write(d)
        print(f'wrote dict to {args.out}, {len(d)} B')
    else:
        gen_file(args.out, args.n_blocks, args.level,
                 args.dict if args.dict else None, args.seed)
