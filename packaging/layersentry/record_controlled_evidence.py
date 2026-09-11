#!/usr/bin/env python3
"""Record operator-controlled LayerSentry CSI qualification evidence.

This helper does not perform or infer a PASS. It only writes a normalized PASS
record after the operator explicitly confirms that one documented controlled
scenario has completed successfully and supplies a non-empty JSON details file.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any

DRIVER = "csi.layersentry.io"
RKE2 = "v1.36.4+rke2r1"
KUBERNETES = "v1.36.4"
ALLOWED_TESTS = {
    "idempotent_retry",
    "duplicate_operations",
    "unknown_reconciliation",
    "tenant_isolation",
    "foreign_csi_isolation",
}


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--test-id", choices=sorted(ALLOWED_TESTS), required=True)
    parser.add_argument("--details-json", type=Path, required=True)
    parser.add_argument("--confirm-pass", action="store_true")
    args = parser.parse_args()

    if not args.confirm_pass:
        raise SystemExit("refusing to record PASS without explicit --confirm-pass")

    root = args.evidence_dir.resolve()
    try:
        state = load_object(root / "state.json")
        details = load_object(args.details_json.resolve())
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise SystemExit(f"invalid controlled evidence input: {exc}")
    if not details:
        raise SystemExit("details JSON must not be empty")

    run_id = str(state.get("run_id") or "").strip()
    storage_class = str(state.get("storage_class") or "").strip()
    if not run_id or not storage_class:
        raise SystemExit("qualification state is missing run_id or storage_class")

    record = {
        "schema_version": 1,
        "product": "LayerSentry CSI",
        "test_id": args.test_id,
        "status": "PASS",
        "recorded_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "run_id": run_id,
        "target": {
            "rke2": RKE2,
            "kubernetes": KUBERNETES,
            "driver": DRIVER,
            "storage_class": storage_class,
        },
        "details": details,
    }
    output = root / f"{args.test_id}.json"
    temp = output.with_suffix(".json.tmp")
    temp.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    temp.replace(output)
    print(f"CONTROLLED_EVIDENCE_RECORDED test={args.test_id} evidence={output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
