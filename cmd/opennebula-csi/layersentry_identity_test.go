//go:build layersentry

// SPDX-License-Identifier: Apache-2.0
package main

import "testing"

func TestLayerSentryRequiresExplicitIdentity(t *testing.T) {
	if err := validateBuildDriverIdentity(layersentryDriverName); err != nil {
		t.Fatalf("expected LayerSentry identity to be accepted: %v", err)
	}
}

func TestLayerSentryRejectsLegacyIdentity(t *testing.T) {
	if err := validateBuildDriverIdentity("csi.opennebula.io"); err == nil {
		t.Fatal("LayerSentry build must reject the legacy identity; use the upstream-compatible build for legacy PVs")
	}
}

func TestLayerSentryRejectsForeignIdentity(t *testing.T) {
	if err := validateBuildDriverIdentity("csi.other.example"); err == nil {
		t.Fatal("LayerSentry build must reject a foreign CSI identity")
	}
}
