#!/bin/sh
# Grab one P15 frame every few seconds for a bounded time (visual check of
# Cammy's screen during trials). Frames stay in private/; no audio, no video file.
# Usage: tools/p15_watch.sh <label> [seconds=240] [interval=5]
set -eu
ROOT=$(cd "$(dirname "$0")/.." && pwd)
OUT=$ROOT/private/reset-diag/p15-$1
DUR=${2:-240}
INT=${3:-5}
mkdir -p "$OUT"
chmod 700 "$ROOT/private/reset-diag" "$OUT"
END=$(( $(date +%s) + DUR ))
while [ "$(date +%s)" -lt "$END" ]; do
  T=$(date +%H%M%S)
  timeout 8 ffmpeg -hide_banner -loglevel error -f v4l2 -video_size 1280x720 -i /dev/video0 \
    -vf "select=gte(n\,5)" -frames:v 1 -q:v 4 -y "$OUT/$T.jpg" </dev/null || echo "grab failed $T"
  sleep "$INT"
done
echo "done $(ls "$OUT" | wc -l) frames in $OUT"
