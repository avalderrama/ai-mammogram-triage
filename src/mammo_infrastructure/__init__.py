"""Infrastructure ring: inference backends (ONNX Runtime + RapidOCR, or PyTorch + EasyOCR) and image adapters
implementing the application ports."""
from .bundle import BundleError
from .registration import Ports, create_ports

__all__ = ["BundleError", "Ports", "create_ports"]
