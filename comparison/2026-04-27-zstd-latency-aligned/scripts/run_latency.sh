#!/usr/bin/env bash
# Latency distribution bench. Single-file full-tar compress/decompress.
# Args: $1 = output dir
set -euo pipefail
OUTDIR="${1:?usage: $0 OUTDIR}"
mkdir -p "$OUTDIR"

# Auto-detect input/refdir/zstd location (9965 vs 6979P)
if [ -d /mnt/oldhome/bjiang7 ]; then
  BASE=/mnt/oldhome/bjiang7
else
  BASE=/home/bjiang7
fi
ZSTD="$BASE/zstd157"
INPUT="$BASE/silesia/silesia_shuf.tar"
REFDIR="$BASE/ref-frames"

N=20
echo "host=$(hostname) zstd=$($ZSTD --version | head -1) input=$INPUT N=$N" | tee "$OUTDIR/info.txt"
sha256sum "$ZSTD" "$INPUT" "$REFDIR"/silesia.l*.zst | tee -a "$OUTDIR/info.txt"

# Pre-warm page cache
cat "$INPUT" > /dev/null
for f in "$REFDIR"/silesia.l*.zst; do cat "$f" > /dev/null; done

CSV="$OUTDIR/latency.csv"
echo "op,level,threads,iter,seconds" > "$CSV"

run_one() {
  local op=$1 level=$2 threads=$3 iter=$4 cmd=$5
  local t
  t=$(python3 -c "import subprocess,time; t0=time.perf_counter(); subprocess.run('$cmd', shell=True, check=True, stdout=subprocess.DEVNULL); print(f'{time.perf_counter()-t0:.6f}')")
  echo "$op,$level,$threads,$iter,$t" >> "$CSV"
}

# Compression: L1/L9/L19/L22 @ T=1 ; L19 @ T=8
for cfg in "1 1" "9 1" "19 1" "22 1" "19 8"; do
  L=$(echo $cfg | awk '{print $1}')
  T=$(echo $cfg | awk '{print $2}')
  if [ "$L" = "22" ]; then ULTRA="--ultra"; else ULTRA=""; fi
  echo "[compress] L=$L T=$T ..."
  for i in $(seq 1 $N); do
    run_one compress $L $T $i "$ZSTD -$L $ULTRA -T$T -q -c $INPUT > /dev/null"
  done
done

# Decompression: L1/L9/L19/L22 @ T=1 (decompress is single-thread anyway)
for L in 1 9 19 22; do
  REF="$REFDIR/silesia.l$L.zst"
  echo "[decompress] L=$L ..."
  for i in $(seq 1 $N); do
    run_one decompress $L 1 $i "$ZSTD -d -T1 -q -c $REF > /dev/null"
  done
done

echo "DONE -> $CSV"
wc -l "$CSV"
