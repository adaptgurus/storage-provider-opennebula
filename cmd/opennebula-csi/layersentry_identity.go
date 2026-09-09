//go:build layersentry

// SPDX-License-Identifier: Apache-2.0
// LayerSentry build variant. Preserve the upstream storage engine and license.
package main

import "flag"

const layersentryDriverName = "csi.layersentry.io"

func init() {
	// main.go registers drivername before package init functions run.
	// Explicit --drivername remains available for a separately tested legacy profile.
	*driverName = layersentryDriverName
	flag.Lookup("drivername").DefValue = layersentryDriverName
}
