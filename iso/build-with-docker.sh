#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE_NAME="lingyue-os-iso-builder"

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is required for local ISO builds." >&2
  echo "Install Docker Desktop, or run the GitHub Actions workflow: Build Lingyue OS ISO." >&2
  exit 1
fi

docker build --platform linux/amd64 -t "$IMAGE_NAME" "$ROOT_DIR/iso"
docker run --rm --privileged \
  --platform linux/amd64 \
  -v "$ROOT_DIR:/workspace" \
  "$IMAGE_NAME"
