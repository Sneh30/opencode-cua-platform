"""OS adapter implementations (ED #11: platform coupling confined here)."""

from .base import (
    AdapterCapabilities,
    AppInfo,
    OSAdapter,
    OSAdapterError,
    OSAdapterPermissionError,
)
from .macos_adapter import MacOSAdapter

__all__ = [
    "AdapterCapabilities",
    "AppInfo",
    "OSAdapter",
    "OSAdapterError",
    "OSAdapterPermissionError",
    "MacOSAdapter",
]