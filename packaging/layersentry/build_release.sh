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

Builds a LayerSentry CSI OCI image, requires embedded SPDX SBOM and max-mode
SLSA provenance attestations, and emits an immutable image reference plus
checksummed release evidence. The script does not push the image.
EOF
  exit 2
}

[[ $# == 2 ]] || usage
version="$1"
out="$2"
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+-layersentry\.[0-9]+$ ]] || usage
release_tag="v${version}"
: "${GO_IMAGE:?GO_IMAGE must be pinned by digest}"
: "${RUNTIME_IMAGE:?RUNTIME_IMAGE must be pinned by digest}"
: "${IMAGE_REF:?IMAGE_REF must be an explicit release tag}"
[[ "$GO_IMAGE" =~ @sha256:[0-9a-f]{64}$ ]] || { echo "GO_IMAGE must use @sha256" >&2; exit 2; }
[[ "$RUNTIME_IMAGE" =~ @sha256:[0-9a-f]{64}$ ]] || { echo "RUNTIME_IMAGE must use @sha256" >&2; exit 2; }
[[ "$IMAGE_REF" =~ :[^/@[:space:]]+$ ]] || { echo "IMAGE_REF must include a tag and must not be a digest reference" >&2; exit 2; }
image_tag="${IMAGE_REF##*:}"
[[ "$image_tag" == "$release_tag" ]] || {
  echo "IMAGE_REF tag must exactly match release version: expected $release_tag, got $image_tag" >&2
  exit 2
}

root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$root"
[[ -z "$(git status --porcelain --untracked-files=normal)" ]] || {
  echo "Refusing provenance-labelled build from a dirty worktree." >&2
  exit 2
}
commit="$(git rev-parse --verify HEAD)"
[[ "$commit" =~ ^[0-9a-f]{40}$ ]] || {
  echo "Source commit is not a full Git SHA: $commit" >&2
  exit 2
}
build_date="$(git show -s --format=%cI HEAD)"
mkdir -p "$out"
out="$(cd "$out" && pwd)"
metadata_file="$out/build-metadata.json"
oci_archive="$out/layersentry-csi.oci.tar"
attestation_dir="$out/attestations"

# Keep full build-record provenance in the metadata file in addition to the
# OCI SLSA attestation attached to the image index.
BUILDX_METADATA_PROVENANCE=max DOCKER_BUILDKIT=1 docker buildx build \
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

python3 packaging/layersentry/verify_oci_attestations.py \
  --oci-archive "$oci_archive" \
  --output-dir "$attestation_dir"

index_digest="$(tr -d '\r\n' < "$attestation_dir/oci-index-digest.txt")"
[[ "$index_digest" =~ ^sha256:[0-9a-f]{64}$ ]] || {
  echo "Verified OCI index digest is malformed: $index_digest" >&2
  exit 2
}
immutable_ref="${IMAGE_REF}@${index_digest}"
printf '%s\n' "$immutable_ref" > "$out/driver-image-ref.txt"
printf '%s\n' "$release_tag" > "$out/release-tag.txt"
printf '%s\n' "$commit" > "$out/source-commit.txt"

# Record Buildx's own result digest as independent build evidence. It may refer
# to the exporter result while the release reference deliberately pins the OCI
# index verified above, which is the object that carries the attestations.
metadata_digest="$(python3 - "$metadata_file" <<'PY'
import json, re, sys
from pathlib import Path
metadata = json.loads(Path(sys.argv[1]).read_text())
value = str(metadata.get("containerimage.digest") or "")
if not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
    raise SystemExit("build-metadata.json is missing a valid containerimage.digest")
print(value)
PY
)"
printf '%s\n' "$metadata_digest" > "$out/buildx-result-digest.txt"

sha256sum "$oci_archive" > "$out/layersentry-csi.oci.tar.sha256"
cat > "$out/source-provenance.json" <<EOF
{
  "source_repository": "adaptgurus/storage-provider-opennebula",
  "source_commit": "$commit",
  "version": "$version",
  "release_tag": "$release_tag",
  "build_date": "$build_date",
  "go_image": "$GO_IMAGE",
  "runtime_image": "$RUNTIME_IMAGE",
  "image_tag": "$IMAGE_REF",
  "immutable_image_ref": "$immutable_ref",
  "buildx_result_digest": "$metadata_digest",
  "oci_index_digest": "$index_digest",
  "oci_archive_sha256_file": "layersentry-csi.oci.tar.sha256",
  "buildkit_metadata": "build-metadata.json",
  "sbom_evidence": "attestations/sbom-attestations.json",
  "provenance_evidence": "attestations/provenance-attestations.json",
  "production_qualified": false
}
EOF

(
  cd "$out"
  sha256sum \
    build-metadata.json \
    buildx-result-digest.txt \
    driver-image-ref.txt \
    release-tag.txt \
    source-commit.txt \
    layersentry-csi.oci.tar \
    layersentry-csi.oci.tar.sha256 \
    source-provenance.json \
    attestations/oci-index-digest.txt \
    attestations/image-manifest-digest.txt \
    attestations/sbom-attestations.json \
    attestations/provenance-attestations.json \
    > release-artifacts.sha256
)

printf 'Built LayerSentry CSI %s from %s with verified SBOM/provenance attestations.\n' "$version" "$commit"
printf 'Expected Git release tag: %s\n' "$release_tag"
printf 'Immutable release image reference: %s\n' "$immutable_ref"
printf 'Live storage qualification is still required before production use.\n'
