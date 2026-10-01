#!/usr/bin/env bash
# Record the screen for a demo (macOS).
#
#   scripts/record.sh <seconds> [name]
#
# Refuses to run anywhere but inside this repo, and refuses when the working
# tree has uncommitted changes. Together that means the only code that can be
# on camera is code that is already public.
#
# Output goes to $BQ_REC_OUT (default ~/Movies/building-quiet), never into the repo.
set -euo pipefail

here="$(cd "$(dirname "$0")/.." && pwd -P)"
top="$(git -C "$PWD" rev-parse --show-toplevel 2>/dev/null || true)"
if [ "$top" != "$here" ]; then
  echo "refusing: recordings only run from inside $(basename "$here"). Current dir: $PWD" >&2
  exit 2
fi
if [ -n "$(git -C "$here" status --porcelain)" ]; then
  echo "refusing: uncommitted changes. Commit or stash first so the recording matches the public repo." >&2
  git -C "$here" status --short >&2
  exit 2
fi

secs="${1:?usage: scripts/record.sh <seconds> [name]}"
name="${2:-demo-$(date +%Y%m%d-%H%M%S)}"
out="${BQ_REC_OUT:-$HOME/Movies/building-quiet}"
mkdir -p "$out"

screencapture -v -V "$secs" "$out/$name.mov"
echo "$out/$name.mov"
