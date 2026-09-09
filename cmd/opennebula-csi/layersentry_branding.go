//go:build layersentry

// Copyright 2026 LayerSentry contributors.
// SPDX-License-Identifier: Apache-2.0

package main

import "flag"

// Only opt-in LayerSentry builds use this identity. Untagged upstream builds
// retain their existing default. Existing volumes must not be renamed in place.
const layerSentryDriverName = "csi.layersentry.io"

func init() {
	*driverName = layerSentryDriverName
	// main.go registers this flag during package variable initialization.
	flag.Lookup("drivername").DefValue = layerSentryDriverName
}
