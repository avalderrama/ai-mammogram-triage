"""Model bundle exported by ai-mammogram-triage-training. Verified at startup.

Two formats, declared by manifest.json ("format"; bundles without it are "torch"):
- torch: PyTorch checkpoints (export_bundle.py), for GPU or research;
- onnx:  the same networks exported to ONNX plus onnx_meta.json (export_onnx.py), for lightweight CPU servers.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

NETWORK_CLASSES = {"gate": 2, "global_512": 3, "global_1024": 3, "patch_2048": 3}
FORMATS = {"torch": (".pt", ()), "onnx": (".onnx", ("onnx_meta.json",))}  # network extension, extra files


class BundleError(Exception):
    """The model bundle is missing, incomplete or corrupted."""


def required_files(bundle_format: str) -> tuple[str, ...]:
    extension, extra = FORMATS[bundle_format]
    return (*(f"{n}{extension}" for n in NETWORK_CLASSES), *extra, "fusion_calibration.json", "manifest.json")


@dataclass(frozen=True)
class ModelBundle:
    root: Path
    version: str
    format: str
    patch_min_source_height: int
    calibration: dict

    def path(self, name: str) -> Path:
        return self.root / name

    def network_path(self, network: str) -> Path:
        return self.root / f"{network}{FORMATS[self.format][0]}"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_bundle(root: Path, verify: bool = True) -> ModelBundle:
    if not (root / "manifest.json").exists():
        raise BundleError(f"Model bundle at {root} is missing: manifest.json")
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    bundle_format = manifest.get("format", "torch")
    if bundle_format not in FORMATS:
        raise BundleError(f"Unknown bundle format '{bundle_format}' (expected one of: {', '.join(FORMATS)})")
    required = required_files(bundle_format)
    missing = [n for n in required if not (root / n).exists()]
    if missing:
        raise BundleError(f"Model bundle at {root} is missing: {', '.join(missing)}")
    if verify:
        unlisted = [n for n in required if n != "manifest.json" and n not in manifest.get("files", {})]
        if unlisted:
            raise BundleError(f"The manifest does not list: {', '.join(unlisted)}")
        for name, info in manifest["files"].items():
            if _sha256(root / name) != info["sha256"]:
                raise BundleError(f"Checksum mismatch for {name}: the bundle is corrupted or was modified")
    calibration = json.loads((root / "fusion_calibration.json").read_text(encoding="utf-8"))
    return ModelBundle(root, manifest["bundle_version"], bundle_format, int(manifest["patch_min_source_height_px"]),
                       calibration)
