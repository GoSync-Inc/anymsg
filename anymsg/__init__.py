"""AnyMsg's HTTP SDK; server and collectors remain operator-managed."""
from .client import AnyMsgError, Client

__version__ = "0.9.10"
__all__ = ["AnyMsgError", "Client", "__version__"]
