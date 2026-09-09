//go:build layersentry

// SPDX-License-Identifier: Apache-2.0
package main

import (
	"flag"
	"testing"
)

func TestLayerSentryIdentityDefault(t *testing.T) {
	if *driverName != layersentryDriverName {
		t.Fatalf("unexpected default identity: %q", *driverName)
	}
	if got := flag.Lookup("drivername").DefValue; got != layersentryDriverName {
		t.Fatalf("help default identity differs: %q", got)
	}
}
