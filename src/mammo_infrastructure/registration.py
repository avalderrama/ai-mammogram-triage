"""Infrastructure registration: the only public entry point of this ring (CA-CFG-002)."""
from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

from mammo_application.abstractions import (BiradsClassifier, MammogramGate, PanelSplitter, PersonalDataMasker,
                                            ScoreFuser, ThresholdsProvider)

from ._adapters import (_BiradsClassifier, _BundleThresholds, _LogisticScoreFuser, _MammogramGate,
                        _OcrPersonalDataMasker, _PanelSplitter)
from .bundle import load_bundle
from .ml.classifiers import GlobalEnsemble, PatchScanner
from .ml.gate_model import GateModel
from .ml.networks import resolve_device
from .ml.text_masking import TextMasker


@dataclass(frozen=True)
class Ports:
    masker: PersonalDataMasker
    splitter: PanelSplitter
    gate: MammogramGate
    classifier: BiradsClassifier
    fuser: ScoreFuser
    thresholds: ThresholdsProvider


def create_ports(models_dir: Path, device_name: str = "auto", verify_bundle: bool = True) -> Ports:
    """Loads the model bundle once (fails fast if it is missing or corrupted) and wires the adapters.
    A single lock serializes GPU inference so concurrent requests are safe."""
    bundle = load_bundle(models_dir, verify=verify_bundle)
    device = resolve_device(device_name)
    lock = threading.Lock()
    gate = GateModel(bundle.path("gate.pt"), device)
    ensemble = GlobalEnsemble([bundle.path("global_512.pt"), bundle.path("global_1024.pt")], device)
    scanner = PatchScanner(bundle.path("patch_2048.pt"), device)
    masker = TextMasker(use_gpu=device.type == "cuda")
    return Ports(
        masker=_OcrPersonalDataMasker(masker, lock),
        splitter=_PanelSplitter(),
        gate=_MammogramGate(gate, lock),
        classifier=_BiradsClassifier(ensemble, scanner, bundle.patch_min_source_height, lock),
        fuser=_LogisticScoreFuser(bundle.calibration),
        thresholds=_BundleThresholds(bundle),
    )
