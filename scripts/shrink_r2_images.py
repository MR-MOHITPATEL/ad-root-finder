"""One-off: shrink every image already in the R2 bucket in place (same key, so
no URLs change). Skips anything that's already small or wouldn't shrink by 10%+.
R2 has no egress fees, so this costs nothing beyond a few thousand free requests.
"""
from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rootfinder import store

_EXT_MIME = store._MIME


def _one(obj: dict) -> tuple[int, int]:
    key, size = obj["Key"], obj["Size"]
    ext = "." + key.rsplit(".", 1)[-1].lower() if "." in key else ""
    if ext not in _EXT_MIME or key.startswith("_healthcheck/") or size <= store._SHRINK_ABOVE:
        return size, size
    try:
        r = store._r2()
        data = r.get_object(Bucket=store.R2_BUCKET, Key=key)["Body"].read()
        small = store.shrink_image(data, ext)
        if len(small) >= len(data):
            return size, size
        r.put_object(Bucket=store.R2_BUCKET, Key=key, Body=small, ContentType=_EXT_MIME[ext])
        return size, len(small)
    except Exception as e:  # noqa: BLE001
        print(f"  failed {key}: {e}", flush=True)
        return size, size


def main() -> None:
    objs = []
    for page in store._r2().get_paginator("list_objects_v2").paginate(Bucket=store.R2_BUCKET):
        objs += page.get("Contents", [])
    print(f"{len(objs)} objects, {sum(o['Size'] for o in objs) / 1e6:.0f} MB before", flush=True)
    before = after = 0
    with ThreadPoolExecutor(max_workers=8) as ex:
        for i, (b, a) in enumerate(ex.map(_one, objs), 1):
            before += b
            after += a
            if i % 200 == 0:
                print(f"  {i}/{len(objs)}  {before / 1e6:.0f} MB -> {after / 1e6:.0f} MB", flush=True)
    print(f"done: {before / 1e6:.0f} MB -> {after / 1e6:.0f} MB "
          f"({100 - 100 * after / max(before, 1):.0f}% smaller)", flush=True)


if __name__ == "__main__":
    main()
