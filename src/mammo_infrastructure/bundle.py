"""Model bundle exported by ai-mammogram-triage-training (export_bundle.py). Verified at startup."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

REQUIRED = ("gate.pt", "global_512.pt", "global_1024.pt", "patch_2048.pt", "fusion_calibration.json",
            "manifest.json")


class BundleError(Exception):
    """The model bundle is missing, incomplete or corrupted."""


@dataclass(frozen=True)
class ModelBundle:
    root: Path
    version: str
    patch_min_source_height: int
    calibration: dict

    def path(self, name: str) -> Path:
        return self.root / name


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_bundle(root: Path, verify: bool = True) -> ModelBundle:
    missing = [n for n in REQUIRED if not (root / n).exists()]
    if missing:
        raise BundleError(f"Model bundle at {root} is missing: {', '.join(missing)}")
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if verify:
        unlisted = [n for n in REQUIRED if n != "manifest.json" and n not in manifest.get("files", {})]
        if unlisted:
            raise BundleError(f"The manifest does not list: {', '.join(unlisted)}")
        for name, info in manifest["files"].items():
            if _sha256(root / name) != info["sha256"]:
                raise BundleError(f"Checksum mismatch for {name}: the bundle is corrupted or was modified")
    calibration = json.loads((root / "fusion_calibration.json").read_text(encoding="utf-8"))
    return ModelBundle(root, manifest["bundle_version"], int(manifest["patch_min_source_height_px"]), calibration)
