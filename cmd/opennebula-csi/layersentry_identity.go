//go:build layersentry

// SPDX-License-Identifier: Apache-2.0
// LayerSentry build policy. Preserve the upstream storage engine and license.
package main

import "fmt"

const layersentryDriverName = "csi.layersentry.io"

func validateBuildDriverIdentity(name string) error {
	if name != layersentryDriverName {
		return fmt.Errorf(
			"LayerSentry build requires explicit --drivername=%s; got %q",
			layersentryDriverName,
			name,
		)
	}
	return nil
}
