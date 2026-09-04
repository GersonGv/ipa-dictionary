#!/usr/bin/env bash
# Build the dictionary database dump from scratch, locally.
#
# Everything happens on this machine — the shared host has neither the memory
# nor the disk for a 3 GB parse. The output is one gzipped .sql file to upload.
#
#   ./tools/build_data.sh              # full run, downloads what it needs
#   MAX_RANK=80000 ./tools/build_data.sh   # a smaller, commoner word list
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD="${BUILD_DIR:-$ROOT/build}"
MAX_RANK="${MAX_RANK:-200000}"
PYTHON="${PYTHON:-$ROOT/.venv/bin/python}"
[ -x "$PYTHON" ] || PYTHON=python3

DUMP="$BUILD/en-wiktextract.jsonl"
FREQ="$BUILD/count_1w.txt"
LEXICON="$BUILD/lexicon.jsonl"
OUT="$BUILD/dictionary.sql.gz"

DUMP_URL="https://kaikki.org/dictionary/English/kaikki.org-dictionary-English.jsonl"
FREQ_URL="https://norvig.com/ngrams/count_1w.txt"

mkdir -p "$BUILD"

# -C - resumes a partial download; this file is ~3 GB and connections drop.
if [ ! -s "$DUMP" ]; then
  echo "==> downloading the English Wiktextract dump (~3 GB, this takes a while)"
  curl -L --retry 3 -C - -o "$DUMP" "$DUMP_URL"
fi

if [ ! -s "$FREQ" ]; then
  echo "==> downloading the frequency list"
  curl -sL -o "$FREQ" "$FREQ_URL"
fi

echo "==> extracting the lexicon (max frequency rank: $MAX_RANK)"
"$PYTHON" "$ROOT/tools/extract_wiktionary.py" \
  --dump "$DUMP" --frequencies "$FREQ" --out "$LEXICON" --max-rank "$MAX_RANK"

echo "==> building the SQL dump"
cd "$ROOT"
"$PYTHON" tools/build_sql.py --lexicon "$LEXICON" --curated data/curated --out "$OUT"

echo
echo "Done: $OUT ($(du -h "$OUT" | cut -f1))"
echo "Next: ./tools/import_remote.sh  (or see docs/DEPLOY.md)"
