"""cce-code-health: code-health metrics and ratchet (experimental 0.x)."""
from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("cce-code-health")
except PackageNotFoundError:  # running from a source tree without installation
    __version__ = "0+unknown"
