#!/usr/bin/env bash
# Reduced matrix: F x D x dict (warm, default pin) + selected cold/numa cells.
set -u
cd "$(dirname "$0")"

PY="${PY:-python3}"
SCRIPT="${SCRIPT:-./bench_pipeline.py}"
OUT="${OUT:-results.csv}"
HOSTLBL="${HOSTLBL:-host}"

HAS_NUMA=0
if command -v numactl >/dev/null 2>&1; then
    if numactl --hardware 2>/dev/null | grep -qE 'available: ([2-9]|[1-9][0-9])'; then
        HAS_NUMA=1
    fi
fi
echo "HAS_NUMA=$HAS_NUMA HOSTLBL=$HOSTLBL"

drop_caches() {
    sync
    if [ -w /proc/sys/vm/drop_caches ]; then
        echo 3 > /proc/sys/vm/drop_caches
    else
        sudo -n sh -c 'sync && echo 3 > /proc/sys/vm/drop_caches' 2>/dev/null || true
    fi
}
warm_up() { for f in "$@"; do cat "$f" > /dev/null; done; }

run_cell() {
    local F=$1 D=$2 DICT=$3 PIN=$4 CACHE=$5
    local files=()
    local DICTARG=""
    if [ "$DICT" = "yes" ]; then
        for i in $(seq 0 $((F-1))); do files+=("data_F${i}_dict.zb"); done
        DICTARG="--dict quant.dict"
    else
        for i in $(seq 0 $((F-1))); do files+=("data_F${i}.zb"); done
    fi
    if [ "$CACHE" = "cold" ]; then drop_caches; else warm_up "${files[@]}"; fi
    local PREFIX=""
    [ "$PIN" = "numa" ] && [ "$HAS_NUMA" = "1" ] && PREFIX="numactl --interleave=all"
    local TAG="${HOSTLBL}_F${F}_D${D}_dict${DICT}_pin${PIN}_${CACHE}"
    echo "=== $TAG ==="
    $PREFIX $PY $SCRIPT --files "${files[@]}" -F $F -D $D $DICTARG \
        --warmup 0 --runs 2 --queue 64 --no-merge \
        --tag "$TAG" --out-csv "$OUT" 2>&1 | tail -4
}

# Main matrix: F in {1,3,6}, D in {F, 2F}, dict in {no,yes}, warm, default pin.
# (F=1 D=2 means single file but 2 decomp threads.)
for F in 1 3 6; do
  for Dmul in 1 2; do
    D=$((F*Dmul))
    [ $D -lt 2 ] && D=2
    for DICT in no yes; do
      run_cell $F $D $DICT default warm
    done
  done
done

# Selected cold cells: F=6, D=12, both dict modes
for DICT in no yes; do
    run_cell 6 12 $DICT default cold
done

# NUMA cells (only if multi-NUMA): F=6, D=12, both dict, warm
if [ "$HAS_NUMA" = "1" ]; then
    for DICT in no yes; do
        run_cell 6 12 $DICT numa warm
    done
fi

echo "ALL DONE -> $OUT"
