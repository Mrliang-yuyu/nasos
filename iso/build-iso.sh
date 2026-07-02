#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ISO_DIR="$ROOT_DIR/iso"
WORK_DIR="$ISO_DIR/work"
DIST_DIR="$ROOT_DIR/dist"
IMAGE_NAME="lyos-v0.1-alpha.iso"

required_files=(index.html styles.css console.html console.css console.js api/lingyue_api.py)
for file in "${required_files[@]}"; do
  if [[ ! -f "$ROOT_DIR/$file" ]]; then
    echo "Missing required web asset: $file" >&2
    exit 1
  fi
done

rm -rf "$WORK_DIR"
mkdir -p "$WORK_DIR" "$DIST_DIR"

rsync -a "$ISO_DIR/live-build/" "$WORK_DIR/"

mkdir -p "$WORK_DIR/config/includes.chroot/opt/lingyue/www"
rsync -a "$ROOT_DIR/index.html" "$ROOT_DIR/styles.css" "$ROOT_DIR/console.html" "$ROOT_DIR/console.css" "$ROOT_DIR/console.js" \
  "$WORK_DIR/config/includes.chroot/opt/lingyue/www/"

mkdir -p "$WORK_DIR/config/includes.chroot/opt/lingyue/api"
rsync -a "$ROOT_DIR/api/lingyue_api.py" "$WORK_DIR/config/includes.chroot/opt/lingyue/api/"

cd "$WORK_DIR"
lb clean --purge || true
lb config
lb build

ISO_PATH="$(find "$WORK_DIR" -maxdepth 1 -type f -name '*.iso' | head -n 1)"
if [[ -z "$ISO_PATH" ]]; then
  echo "ISO build completed, but no .iso file was found in $WORK_DIR" >&2
  exit 1
fi

cp "$ISO_PATH" "$DIST_DIR/$IMAGE_NAME"
sha256sum "$DIST_DIR/$IMAGE_NAME" > "$DIST_DIR/$IMAGE_NAME.sha256"

echo "Built $DIST_DIR/$IMAGE_NAME"
echo "Checksum $DIST_DIR/$IMAGE_NAME.sha256"
