//go:build layersentry

// SPDX-License-Identifier: Apache-2.0
package driver

import "github.com/container-storage-interface/spec/lib/go/csi"

// LayerSentry deliberately advertises only the controller capabilities that are
// in the current qualification scope. The underlying upstream engine contains
// additional backend-specific expansion, snapshot and clone paths, but those
// capabilities must not be advertised by a LayerSentry release until a named
// storage profile has passed the corresponding live qualification matrix.
//
// This assignment affects only builds made with -tags layersentry. The untagged
// upstream-compatible build retains its existing capability set.
func init() {
	controllerCapabilityTypes = []csi.ControllerServiceCapability_RPC_Type{
		csi.ControllerServiceCapability_RPC_CREATE_DELETE_VOLUME,
		csi.ControllerServiceCapability_RPC_PUBLISH_UNPUBLISH_VOLUME,
		csi.ControllerServiceCapability_RPC_LIST_VOLUMES,
		csi.ControllerServiceCapability_RPC_GET_CAPACITY,
	}
}
