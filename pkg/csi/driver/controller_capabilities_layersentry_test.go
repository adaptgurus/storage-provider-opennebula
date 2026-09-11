//go:build layersentry

// SPDX-License-Identifier: Apache-2.0
package driver

import (
	"context"
	"testing"

	"github.com/container-storage-interface/spec/lib/go/csi"
	"github.com/stretchr/testify/require"
)

func TestLayerSentryControllerCapabilitiesExcludeUnqualifiedOptionalRPCs(t *testing.T) {
	server := &ControllerServer{}
	resp, err := server.ControllerGetCapabilities(context.Background(), &csi.ControllerGetCapabilitiesRequest{})
	require.NoError(t, err)

	got := make(map[csi.ControllerServiceCapability_RPC_Type]bool, len(resp.GetCapabilities()))
	for _, capability := range resp.GetCapabilities() {
		if capability == nil || capability.GetRpc() == nil {
			continue
		}
		got[capability.GetRpc().GetType()] = true
	}

	for _, required := range []csi.ControllerServiceCapability_RPC_Type{
		csi.ControllerServiceCapability_RPC_CREATE_DELETE_VOLUME,
		csi.ControllerServiceCapability_RPC_PUBLISH_UNPUBLISH_VOLUME,
		csi.ControllerServiceCapability_RPC_LIST_VOLUMES,
		csi.ControllerServiceCapability_RPC_GET_CAPACITY,
	} {
		require.Truef(t, got[required], "required capability %s was not advertised", required.String())
	}

	for _, unqualified := range []csi.ControllerServiceCapability_RPC_Type{
		csi.ControllerServiceCapability_RPC_EXPAND_VOLUME,
		csi.ControllerServiceCapability_RPC_CLONE_VOLUME,
		csi.ControllerServiceCapability_RPC_CREATE_DELETE_SNAPSHOT,
	} {
		require.Falsef(t, got[unqualified], "unqualified capability %s must not be advertised", unqualified.String())
	}
}
