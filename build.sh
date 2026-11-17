#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <image-tag>" >&2
  exit 1
fi

tag="$1"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
image="${IMAGE_PREFIX:-llsynit}/${MODULE_NAME:-bok_to_pef}"
platforms="${PLATFORMS:-linux/amd64,linux/arm64}"
dockerfile="${DOCKERFILE:-${script_dir}/Dockerfile}"
push_latest="${PUSH_LATEST:-0}"

if [[ ! -f "$dockerfile" ]]; then
  echo "Dockerfile not found: $dockerfile" >&2
  exit 2
fi

tags=(-t "${image}:${tag}")
case "$push_latest" in
  0) ;;
  1) tags+=(-t "${image}:latest") ;;
  *) echo "PUSH_LATEST must be 0 or 1" >&2; exit 2 ;;
esac

docker buildx inspect multi >/dev/null 2>&1 ||
  docker buildx create --name multi >/dev/null

echo "Building ${image}:${tag} for ${platforms}"
docker buildx build \
  --builder multi \
  --platform "$platforms" \
  "${tags[@]}" \
  -f "$dockerfile" \
  --push "$script_dir"

printf 'Pushed %s\n' "${image}:${tag}"
if [[ "$push_latest" == 1 ]]; then
  printf 'Pushed %s\n' "${image}:latest"
fi
