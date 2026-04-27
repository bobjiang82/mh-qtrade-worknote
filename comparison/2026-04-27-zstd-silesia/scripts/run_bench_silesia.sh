#!/bin/bash
# zstd benchmark on fixed silesia_shuf.tar corpus
set -u
WORKDIR="${WORKDIR:-$(pwd)}"
CORPUS="${CORPUS:-${WORKDIR}/silesia/silesia_shuf.tar}"
OUTDIR="${OUTDIR:-${WORKDIR}/zstd-bench-silesia}"
mkdir -p "$OUTDIR/raw"

if [ ! -f "$CORPUS" ]; then
    echo "ERROR: corpus not found: $CORPUS" >&2
    exit 1
fi

echo "=== zstd benchmark on silesia ===" | tee "$OUTDIR/run.log"
echo "host: $(hostname)" | tee -a "$OUTDIR/run.log"
echo "date: $(date -Is)" | tee -a "$OUTDIR/run.log"
echo "corpus: $CORPUS" | tee -a "$OUTDIR/run.log"
echo "corpus size: $(stat -c%s "$CORPUS") bytes" | tee -a "$OUTDIR/run.log"
echo "corpus sha256: $(sha256sum "$CORPUS" | awk '{print $1}')" | tee -a "$OUTDIR/run.log"
echo "zstd: $(zstd --version | head -1)" | tee -a "$OUTDIR/run.log"

run() {
    local tag="$1"; shift
    local out="$OUTDIR/raw/${tag}.log"
    echo "+++ $tag : zstd -b $* $CORPUS" | tee -a "$OUTDIR/run.log"
    zstd -b "$@" "$CORPUS" 2>&1 | tee "$out" | tail -3 | tee -a "$OUTDIR/run.log"
}

# L19 single-thread baseline
run "l19_t1"     -19 -T1 -i10

# L19 thread scaling
for T in 2 4 8 16 32 64 0; do
    run "l19_t${T}" -19 -T${T} -i10
done

# Level sweep at T=1
for L in 1 3 9 19 22; do
    if [ "$L" = "22" ]; then
        run "l${L}_t1" --ultra -${L} -T1 -i10
    else
        run "l${L}_t1" -${L} -T1 -i10
    fi
done

echo "=== done at $(date -Is) ===" | tee -a "$OUTDIR/run.log"
ls -la "$OUTDIR/raw/"
