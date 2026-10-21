"""Onion contracts (pyproject [tool.importlinter]) must hold; infrastructure exposes only its registration API."""
import ast
import os
import shutil
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"


def test_import_contracts_are_kept():
    exe = shutil.which("lint-imports") or str(Path(sys.executable).with_name("lint-imports"))
    proc = subprocess.run([exe], cwd=SRC.parent, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_only_the_host_imports_infrastructure():
    offenders = []
    for path in SRC.rglob("*.py"):
        package = path.relative_to(SRC).parts[0]
        if package in ("mammo_host", "mammo_infrastructure"):
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                [node.module or ""] if isinstance(node, ast.ImportFrom) else []
            offenders += [f"{path.name}: {n}" for n in names if n.startswith("mammo_infrastructure")]
    assert offenders == []


BACKEND_LIBRARIES = {
    "mammo_infrastructure/ml/torch_backend.py": {"torch", "timm", "easyocr"},
    "mammo_infrastructure/ml/onnx_backend.py": {"onnxruntime", "rapidocr_onnxruntime"},
}


def test_each_inference_library_is_imported_only_by_its_backend():
    offenders = []
    for path in SRC.rglob("*.py"):
        rel = path.relative_to(SRC).as_posix()
        allowed = BACKEND_LIBRARIES.get(rel, set())
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                [node.module or ""] if isinstance(node, ast.ImportFrom) and node.level == 0 else []
            for name in names:
                root = name.split(".")[0]
                if any(root in libs for libs in BACKEND_LIBRARIES.values()) and root not in allowed:
                    offenders.append(f"{rel}: {name}")
    assert offenders == []


def test_starting_the_host_loads_no_inference_library():
    """Backends are imported only when a bundle selects them: an ONNX server never loads PyTorch."""
    code = ("import sys, mammo_host.composition, mammo_infrastructure; "
            "print(sorted(m for m in ('torch', 'timm', 'easyocr', 'onnxruntime', 'rapidocr_onnxruntime') "
            "if m in sys.modules))")
    proc = subprocess.run([sys.executable, "-c", code], cwd=SRC.parent, capture_output=True, text=True,
                          env={**os.environ, "PYTHONPATH": str(SRC)})
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "[]"


def test_host_uses_only_the_public_registration_api():
    text = (SRC / "mammo_host" / "composition.py").read_text(encoding="utf-8")
    assert "mammo_infrastructure._" not in text and "mammo_infrastructure.ml" not in text
