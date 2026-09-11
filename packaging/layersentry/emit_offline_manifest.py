#!/usr/bin/env python3
"""Emit the exact digest-pinned images required by a LayerSentry CSI release."""
from __future__ import annotations

import argparse
from pathlib import Path

from build_chart import REQUIRED_SIDE_CARS, load_release_lock


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-lock", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    lock = load_release_lock(args.release_lock.resolve())
    images = [lock["images"]["driver"]]
    images.extend(lock["images"][name] for name in REQUIRED_SIDE_CARS)
    if lock["images"].get("snapshotter"):
        images.append(lock["images"]["snapshotter"])

    unique = list(dict.fromkeys(images))
    args.output.write_text("\n".join(unique) + "\n")
    print(f"Wrote {len(unique)} immutable image references to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
