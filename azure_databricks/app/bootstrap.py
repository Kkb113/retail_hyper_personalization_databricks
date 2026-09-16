"""Reassemble the public semantic snapshot before verified App startup."""

import hashlib
import json
from pathlib import Path


def assemble(root):
    manifest = json.loads((root / "manifest.json").read_text())
    parts = sorted(name for name in manifest if name.startswith("semantic-part-"))
    if not parts or len(parts) > 10:
        raise RuntimeError("Invalid semantic parts")
    data = bytearray()
    for name in parts:
        if "/" in name or "\\" in name:
            raise RuntimeError("Invalid semantic part path")
        part = (root / name).read_bytes()
        if hashlib.sha256(part).hexdigest() != manifest[name]:
            raise RuntimeError("Semantic part hash mismatch")
        data.extend(part)
        if len(data) > 80_000_000:
            raise RuntimeError("Semantic snapshot too large")
    if hashlib.sha256(data).hexdigest() != manifest["semantic.json"]:
        raise RuntimeError("Semantic snapshot hash mismatch")
    (root / "semantic.json").write_bytes(data)


if __name__ == "__main__":
    assemble(Path.cwd())
    from retail_hp_azure.phase10_release import main

    main()
