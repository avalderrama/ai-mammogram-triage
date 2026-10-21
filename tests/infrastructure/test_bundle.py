"""Model bundle verification for both formats, with placeholder files (no real weights)."""
import hashlib
import json
import sys

import pytest

import mammo_infrastructure.ml
from mammo_infrastructure import BundleError, create_ports
from mammo_infrastructure.bundle import load_bundle, required_files


def write_bundle(root, bundle_format=None, skip=(), tamper=None):
    files = {}
    for name in required_files(bundle_format if bundle_format in ("torch", "onnx") else "torch"):
        if name == "manifest.json" or name in skip:
            continue
        data = b"{}" if name.endswith(".json") else name.encode()
        (root / name).write_bytes(data)
        files[name] = {"sha256": hashlib.sha256(data).hexdigest()}
    if tamper:
        (root / tamper).write_bytes(b"modified")
    manifest = {"bundle_version": "9.9.9", "patch_min_source_height_px": 1500, "files": files}
    if bundle_format:
        manifest["format"] = bundle_format
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


def test_bundles_without_a_format_are_pytorch(tmp_path):
    bundle = load_bundle(write_bundle(tmp_path))
    assert bundle.format == "torch" and bundle.network_path("gate").name == "gate.pt"


def test_onnx_bundle_needs_its_networks_and_metadata(tmp_path):
    bundle = load_bundle(write_bundle(tmp_path, "onnx"))
    assert bundle.format == "onnx" and bundle.network_path("patch_2048").name == "patch_2048.onnx"
    assert "onnx_meta.json" in required_files("onnx")


def test_missing_onnx_metadata_is_reported(tmp_path):
    with pytest.raises(BundleError, match="onnx_meta.json"):
        load_bundle(write_bundle(tmp_path, "onnx", skip=("onnx_meta.json",)))


def test_unknown_format_is_rejected(tmp_path):
    with pytest.raises(BundleError, match="Unknown bundle format"):
        load_bundle(write_bundle(tmp_path, "tensorflow"))


def test_tampered_onnx_network_is_rejected(tmp_path):
    with pytest.raises(BundleError, match="Checksum mismatch for gate.onnx"):
        load_bundle(write_bundle(tmp_path, "onnx", tamper="gate.onnx"))


def test_missing_manifest_is_reported(tmp_path):
    with pytest.raises(BundleError, match="manifest.json"):
        load_bundle(tmp_path)


def test_onnx_bundle_refuses_cuda_before_loading_anything(tmp_path):
    with pytest.raises(BundleError, match="run on CPU"):
        create_ports(write_bundle(tmp_path, "onnx"), "cuda")


def test_onnx_bundle_with_incomplete_metadata_is_rejected(tmp_path):
    pytest.importorskip("onnxruntime")
    with pytest.raises(BundleError, match="lacks the metadata of: gate, global_512, global_1024, patch_2048"):
        create_ports(write_bundle(tmp_path, "onnx"), "auto")  # the placeholder onnx_meta.json is "{}"


def test_missing_backend_extra_explains_what_to_install(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "onnxruntime", None)  # as if the extra were not installed
    monkeypatch.delitem(sys.modules, "mammo_infrastructure.ml.onnx_backend", raising=False)
    monkeypatch.delattr(mammo_infrastructure.ml, "onnx_backend", raising=False)
    with pytest.raises(BundleError, match=r'pip install "\.\[onnx\]"'):
        create_ports(write_bundle(tmp_path, "onnx"), "auto")
