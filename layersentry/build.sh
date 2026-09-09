#!/usr/bin/env bash
# Copyright 2026 LayerSentry contributors. SPDX-License-Identifier: Apache-2.0
set -euo pipefail
if [[ $# != 1 || ! $1 =~ ^[0-9]+\.[0-9]+\.[0-9]+-layersentry\.[0-9]+$ ]]; then
  echo 'Usage: layersentry/build.sh X.Y.Z-layersentry.N' >&2
  exit 2
fi
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
version="$1"
commit="$(git rev-parse --verify HEAD)"
if [[ -n "$(git status --porcelain --untracked-files=normal)" ]]; then
  echo 'Refusing a provenance-labelled build from a dirty worktree.' >&2
  exit 2
fi
build_date="$(git show -s --format=%cI HEAD)"
metadata='github.com/OpenNebula/storage-provider-opennebula/pkg/csi/driver'
mkdir -p bin
CGO_ENABLED=0 go build -mod=readonly -trimpath -tags layersentry \
  -ldflags "-X ${metadata}.driverVersion=${version} -X ${metadata}.driverCommit=${commit} -X ${metadata}.driverBuildDate=${build_date}" \
  -o bin/layersentry-csi ./cmd/opennebula-csi
printf 'Built %s from %s. Not a production certification.\n' "$version" "$commit"
