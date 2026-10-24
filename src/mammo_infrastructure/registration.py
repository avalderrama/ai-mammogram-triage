"""Infrastructure registration: the only public entry point of this ring (CA-CFG-002)."""
from __future__ import annotations

import json
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from mammo_application.abstractions import (BiradsClassifier, MammogramGate, PanelSplitter, PersonalDataMasker,
                                            ScoreFuser, ThresholdsProvider)

from ._adapters import (_BiradsClassifier, _BundleThresholds, _LogisticScoreFuser, _MammogramGate,
                        _OcrPersonalDataMasker, _PanelSplitter)
from .bundle import BundleError, ModelBundle, load_bundle
from .ml.classifiers import GlobalEnsemble, PatchScanner
from .ml.gate_model import GateModel
from .ml.inference import Backend
from .ml.text_masking import TextMasker


@dataclass(frozen=True)
class Ports:
    masker: PersonalDataMasker
    splitter: PanelSplitter
    gate: MammogramGate
    classifier: BiradsClassifier
    fuser: ScoreFuser
    thresholds: ThresholdsProvider


def _load_backend(bundle: ModelBundle, device_name: str) -> Backend:
    """The bundle's format picks the backend. Imported lazily, so the other one is never loaded (an ONNX server does
    not even need PyTorch installed)."""
    if bundle.format == "onnx" and device_name == "cuda":
        raise BundleError("ONNX bundles run on CPU: set MAMMO_DEVICE to auto or cpu, or use a PyTorch bundle for GPU")
    try:
        if bundle.format == "onnx":
            from .ml import onnx_backend
            return onnx_backend.load_backend(bundle)
        from .ml import torch_backend
    except ImportError as exc:
        raise BundleError(f"The bundle at {bundle.root} is in '{bundle.format}' format, but its backend is not "
                          f"installed ({exc.name} is missing): pip install \".[{bundle.format}]\"") from exc
    return torch_backend.load_backend(bundle, device_name)


def _load_shadow_gate(gate_dir: Path, verify_bundle: bool) -> GateModel:
    """Only the gate of another ONNX bundle (verified like any bundle): its other networks are not loaded."""
    bundle = load_bundle(gate_dir, verify=verify_bundle)
    if bundle.format != "onnx":
        raise BundleError(f"The shadow gate bundle at {gate_dir} must be in ONNX format")
    from .ml import onnx_backend
    metas = json.loads(bundle.path("onnx_meta.json").read_text(encoding="utf-8"))
    return GateModel(onnx_backend.load_network(bundle.network_path("gate"), "gate", metas["gate"]))


def create_ports(models_dir: Path, device_name: str = "auto", verify_bundle: bool = True,
                 shadow_gate_dir: Path | None = None,
                 on_gate_comparison: Callable[[bool, bool], None] | None = None) -> Ports:
    """Loads the model bundle once (fails fast if it is missing or corrupted) and wires the adapters.
    A single lock serializes inference so concurrent requests are safe. With shadow_gate_dir, the gate of that bundle
    also judges every view, only to be compared with the gate in use (on_gate_comparison)."""
    bundle = load_bundle(models_dir, verify=verify_bundle)
    backend = _load_backend(bundle, device_name)
    nets = backend.networks
    lock = threading.Lock()
    gate = GateModel(nets["gate"])
    ensemble = GlobalEnsemble([(nets[n].network, int(nets[n].meta["height"])) for n in ("global_512", "global_1024")])
    scanner = PatchScanner(nets["patch_2048"].network, backend.patch_batch_size)
    masker = TextMasker(backend.text_detector)
    return Ports(
        masker=_OcrPersonalDataMasker(masker, lock),
        splitter=_PanelSplitter(),
        gate=_MammogramGate(gate, lock, _load_shadow_gate(shadow_gate_dir, verify_bundle) if shadow_gate_dir else None,
                            on_gate_comparison),
        classifier=_BiradsClassifier(ensemble, scanner, bundle.patch_min_source_height, lock),
        fuser=_LogisticScoreFuser(bundle.calibration),
        thresholds=_BundleThresholds(bundle),
    )
