#!/usr/bin/env bash
set -euo pipefail
cd /mnt/oldhome/bjiang7/zstd-bench
mkdir -p raw

SIZE_MB=1024  # 1 GiB per dataset

log() { echo "[$(date +%H:%M:%S)] $*"; }

# ---------- 1. Prepare datasets ----------
if [[ ! -f random.bin ]]; then
  log "Generating random.bin (${SIZE_MB} MiB)"
  head -c $((SIZE_MB*1024*1024)) </dev/urandom > random.bin
fi
if [[ ! -f zeros.bin ]]; then
  log "Generating zeros.bin"
  head -c $((SIZE_MB*1024*1024)) </dev/zero > zeros.bin
fi
if [[ ! -f text.bin ]]; then
  log "Building text.bin from /usr (source-like content)"
  TARGET=$((SIZE_MB*1024*1024))
  : > text.bin.seed
  # Collect a seed of readable text content
  find /usr/share/doc /usr/include /usr/share/man /usr/share/locale -type f 2>/dev/null \
    | head -50000 \
    | xargs -r cat 2>/dev/null >> text.bin.seed || true
  SEED_SZ=$(stat -c%s text.bin.seed)
  log "  seed size = $SEED_SZ bytes"
  if [[ $SEED_SZ -lt 1048576 ]]; then
    log "  seed too small, falling back to /etc + /var/log"
    find /etc /var/log -type f 2>/dev/null | head -10000 | xargs -r cat 2>/dev/null >> text.bin.seed || true
    SEED_SZ=$(stat -c%s text.bin.seed)
  fi
  cp text.bin.seed text.bin
  while [[ $(stat -c%s text.bin) -lt $TARGET ]]; do
    cat text.bin.seed >> text.bin
  done
  truncate -s $TARGET text.bin
  rm -f text.bin.seed
fi

ls -lh random.bin zeros.bin text.bin

# ---------- 2. System snapshot ----------
{
  echo "=== date ==="; date
  echo "=== uname ==="; uname -a
  echo "=== zstd ==="; zstd --version
  echo "=== cpu ==="; lscpu | head -25
  echo "=== mem ==="; free -h
  echo "=== disk ==="; df -h /mnt/oldhome
} > system.txt

# ---------- 3. CSV header ----------
CSV=results.csv
echo "dataset,level,threads,mode,ratio,c_speed_MBps,d_speed_MBps,raw_log" > $CSV

# Parse a zstd -b output line:
#  19#random.bin       :1073741824 ->1073741909 (x1.000),  41.7 MB/s,  843.4 MB/s
parse_and_append() {
  local dataset="$1" level="$2" threads="$3" mode="$4" logfile="$5"
  # Take last matching line in case of multi-iteration output
  local line
  line=$(grep -E "^[[:space:]]*${level}#" "$logfile" | tail -1 || true)
  if [[ -z "$line" ]]; then
    echo "${dataset},${level},${threads},${mode},,,,${logfile}" >> $CSV
    return
  fi
  # Extract ratio (xN.NNN), c_speed and d_speed
  local ratio cspd dspd
  ratio=$(echo "$line" | grep -oE 'x[0-9]+\.[0-9]+' | head -1 | tr -d 'x')
  cspd=$(echo "$line" | grep -oE '[0-9]+\.[0-9]+ MB/s' | sed -n '1p' | awk '{print $1}')
  dspd=$(echo "$line" | grep -oE '[0-9]+\.[0-9]+ MB/s' | sed -n '2p' | awk '{print $1}')
  echo "${dataset},${level},${threads},${mode},${ratio},${cspd},${dspd},${logfile}" >> $CSV
}

run_one() {
  local dataset="$1" level="$2" threads="$3" extra="$4"
  local tag="L${level}_T${threads}"
  local logfile="raw/${dataset%.bin}_${tag}.log"
  log "  [$dataset] level=$level threads=$threads $extra"
  # -b for compress+decompress at given level, -i10 = at least 10s
  zstd -b${level} -T${threads} -i10 $extra "$dataset" > "$logfile" 2>&1 || {
    log "  ! failed (see $logfile)"; return; }
  parse_and_append "$dataset" "$level" "$threads" "cd" "$logfile"
}

# ---------- 4. Main matrix ----------
DATASETS=(random.bin text.bin zeros.bin)

# (a) Level 19 single-thread baseline
for d in "${DATASETS[@]}"; do
  run_one "$d" 19 1 ""
done

# (b) Level 19 multi-thread scaling (compression scales; decomp typically single-thread)
for d in "${DATASETS[@]}"; do
  for t in 2 4 8 16 32 64 0; do
    run_one "$d" 19 $t ""
  done
done

# (c) Level sweep at single thread, only on text.bin (most representative)
for lvl in 1 3 9 22; do
  extra=""
  [[ $lvl -ge 20 ]] && extra="--ultra"
  run_one text.bin $lvl 1 "$extra"
done

log "DONE. CSV: $CSV"
column -s, -t < $CSV
