# SPDX-License-Identifier: Apache-2.0
# Supply approved image@sha256 references; no guessed release pins or floating defaults.
ARG GO_IMAGE
ARG RUNTIME_IMAGE
FROM ${GO_IMAGE} AS builder
ARG TARGETOS=linux
ARG TARGETARCH=amd64
ARG VERSION=0.1.0-dev
ARG COMMIT=unknown
ARG BUILD_DATE=unknown
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY cmd/ cmd/
COPY pkg/ pkg/
RUN CGO_ENABLED=0 GOOS=${TARGETOS} GOARCH=${TARGETARCH} go build -mod=readonly -trimpath -tags layersentry \
    -ldflags "-X github.com/OpenNebula/storage-provider-opennebula/pkg/csi/driver.driverVersion=${VERSION} -X github.com/OpenNebula/storage-provider-opennebula/pkg/csi/driver.driverCommit=${COMMIT} -X github.com/OpenNebula/storage-provider-opennebula/pkg/csi/driver.driverBuildDate=${BUILD_DATE}" \
    -o /out/layersentry-csi ./cmd/opennebula-csi

FROM ${RUNTIME_IMAGE}
ARG VERSION=0.1.0-dev
ARG COMMIT=unknown
ARG BUILD_DATE=unknown
LABEL org.opencontainers.image.title="LayerSentry CSI" \
      org.opencontainers.image.description="LayerSentry-qualified candidate using the OpenNebula CSI engine" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${COMMIT}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.source="https://github.com/adaptgurus/storage-provider-opennebula" \
      org.opencontainers.image.licenses="Apache-2.0"
# RUNTIME_IMAGE must be the qualified Alpine-based CSI runtime with these tools,
# assembled/scanned/pinned separately: ceph-common, ceph-fuse, ext4/xfs utilities,
# util-linux and udev. This file deliberately does not install moving packages.
WORKDIR /app
COPY --from=builder /out/layersentry-csi /app/layersentry-csi
ENTRYPOINT ["/app/layersentry-csi"]
