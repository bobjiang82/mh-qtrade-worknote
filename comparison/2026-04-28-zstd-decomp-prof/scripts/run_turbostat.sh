#!/usr/bin/env bash
# Run zstd decompress under turbostat to capture actual freq (APERF/MPERF) + power.
set -euo pipefail
OUTDIR="${1:?usage: $0 OUTDIR}"
mkdir -p "$OUTDIR"

if [ -d /mnt/oldhome/bjiang7 ]; then BASE=/mnt/oldhome/bjiang7; else BASE=/home/bjiang7; fi
ZSTD="$BASE/zstd157"
REFDIR="$BASE/ref-frames"

# Pre-warm
for f in "$REFDIR"/silesia.l*.zst; do cat "$f" > /dev/null; done

run_with_turbostat() {
  local L=$1 N=$2
  local ref="$REFDIR/silesia.l$L.zst"
  local out="$OUTDIR/turbostat_l${L}.txt"
  echo "[L=$L N=$N] ..."
  # Start turbostat in background, summary every 1s, restrict to CPU 0
  turbostat --quiet --interval 1 --cpu 0 \
    --show CPU,Bzy_MHz,TSC_MHz,IPC,CoreTmp,PkgWatt,RAMWatt 2>&1 > "$out" &
  TS_PID=$!
  sleep 1
  for i in $(seq 1 $N); do
    numactl --cpunodebind=0 --membind=0 taskset -c 0 \
      "$ZSTD" -d -T1 -q -c "$ref" > /dev/null
  done
  sleep 1
  kill -INT $TS_PID 2>/dev/null || true
  wait $TS_PID 2>/dev/null || true
}

# Loop several times so we get enough 1s rows under load
run_with_turbostat 9 8
run_with_turbostat 19 8
run_with_turbostat 22 5

echo "DONE -> $OUTDIR"
ls -la "$OUTDIR"
