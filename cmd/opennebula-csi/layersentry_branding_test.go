//go:build layersentry

// Copyright 2026 LayerSentry contributors.
// SPDX-License-Identifier: Apache-2.0

package main

import (
	"flag"
	"testing"
)

func TestLayerSentryDefaultIdentity(t *testing.T) {
	if got := *driverName; got != layerSentryDriverName {
		t.Fatalf("default driver identity = %q, want %q", got, layerSentryDriverName)
	}
}

func TestLayerSentryHelpDefault(t *testing.T) {
	f := flag.Lookup("drivername")
	if f == nil || f.DefValue != layerSentryDriverName {
		t.Fatalf("drivername help does not describe the LayerSentry default")
	}
}

func TestLayerSentryExplicitCompatibilityIdentity(t *testing.T) {
	f := flag.Lookup("drivername")
	old := f.Value.String()
	t.Cleanup(func() { _ = f.Value.Set(old) })
	if err := f.Value.Set("csi.opennebula.io"); err != nil {
		t.Fatal(err)
	}
	if got := *driverName; got != "csi.opennebula.io" {
		t.Fatalf("explicit compatibility override not respected: %q", got)
	}
}
