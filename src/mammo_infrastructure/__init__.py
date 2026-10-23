"""Infrastructure ring: inference backends (ONNX Runtime + RapidOCR, or PyTorch + EasyOCR) and image adapters
implementing the application ports."""
from .bundle import BundleError
from .registration import Ports, create_ports
from .usage_log import JsonUsageLog, NoUsageLog

__all__ = ["BundleError", "JsonUsageLog", "NoUsageLog", "Ports", "create_ports"]
