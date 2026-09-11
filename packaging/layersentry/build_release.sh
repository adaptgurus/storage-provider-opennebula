#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage:
  GO_IMAGE=<image@sha256:...> \
  RUNTIME_IMAGE=<image@sha256:...> \
  IMAGE_REF=<registry/repo:tag> \
  packaging/layersentry/build_release.sh <version> <output-dir>

Builds a LayerSentry CSI OCI image with BuildKit SBOM and SLSA provenance
attestations. The output is a local OCI archive; this script does not push.
EOF
  exit 2
}

[[ $# == 2 ]] || usage
version="$1"
out="$2"
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+-layersentry\.[0-9]+$ ]] || usage
: "${GO_IMAGE:?GO_IMAGE must be pinned by digest}"
: "${RUNTIME_IMAGE:?RUNTIME_IMAGE must be pinned by digest}"
: "${IMAGE_REF:?IMAGE_REF must be an explicit release tag}"
[[ "$GO_IMAGE" =~ @sha256:[0-9a-f]{64}$ ]] || { echo "GO_IMAGE must use @sha256" >&2; exit 2; }
[[ "$RUNTIME_IMAGE" =~ @sha256:[0-9a-f]{64}$ ]] || { echo "RUNTIME_IMAGE must use @sha256" >&2; exit 2; }
[[ "$IMAGE_REF" =~ :[^/@[:space:]]+$ ]] || { echo "IMAGE_REF must include a tag and must not be a digest reference" >&2; exit 2; }

root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$root"
[[ -z "$(git status --porcelain --untracked-files=normal)" ]] || {
  echo "Refusing provenance-labelled build from a dirty worktree." >&2
  exit 2
}
commit="$(git rev-parse --verify HEAD)"
build_date="$(git show -s --format=%cI HEAD)"
mkdir -p "$out"
out="$(cd "$out" && pwd)"
metadata_file="$out/build-metadata.json"
oci_archive="$out/layersentry-csi.oci.tar"

DOCKER_BUILDKIT=1 docker buildx build \
  --file packaging/layersentry/Dockerfile \
  --platform linux/amd64 \
  --build-arg "GO_IMAGE=$GO_IMAGE" \
  --build-arg "RUNTIME_IMAGE=$RUNTIME_IMAGE" \
  --build-arg "VERSION=$version" \
  --build-arg "COMMIT=$commit" \
  --build-arg "BUILD_DATE=$build_date" \
  --tag "$IMAGE_REF" \
  --sbom=true \
  --provenance=mode=max \
  --metadata-file "$metadata_file" \
  --output "type=oci,dest=$oci_archive" \
  .

sha256sum "$oci_archive" > "$out/layersentry-csi.oci.tar.sha256"
cat > "$out/source-provenance.json" <<EOF
{
  "source_repository": "adaptgurus/storage-provider-opennebula",
  "source_commit": "$commit",
  "version": "$version",
  "build_date": "$build_date",
  "go_image": "$GO_IMAGE",
  "runtime_image": "$RUNTIME_IMAGE",
  "image_tag": "$IMAGE_REF",
  "oci_archive_sha256_file": "layersentry-csi.oci.tar.sha256",
  "buildkit_metadata": "build-metadata.json",
  "sbom_attestation": "embedded in OCI image index by buildx --sbom=true",
  "provenance_attestation": "embedded in OCI image index by buildx --provenance=mode=max",
  "production_qualified": false
}
EOF

printf 'Built LayerSentry CSI %s from %s with SBOM/provenance attestations.\n' "$version" "$commit"
printf 'Live storage qualification is still required before production use.\n'
