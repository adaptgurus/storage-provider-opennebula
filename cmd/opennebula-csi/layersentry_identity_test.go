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
	f := flag.Lookup("drivername")
	if f == nil {
		t.Fatal("drivername flag is not registered")
	}
	if got := f.DefValue; got != layersentryDriverName {
		t.Fatalf("help default identity differs: %q", got)
	}
}

func TestLayerSentryExplicitCompatibilityIdentity(t *testing.T) {
	f := flag.Lookup("drivername")
	if f == nil {
		t.Fatal("drivername flag is not registered")
	}
	old := f.Value.String()
	t.Cleanup(func() { _ = f.Value.Set(old) })
	if err := f.Value.Set("csi.opennebula.io"); err != nil {
		t.Fatal(err)
	}
	if got := *driverName; got != "csi.opennebula.io" {
		t.Fatalf("explicit compatibility override not respected: %q", got)
	}
}
