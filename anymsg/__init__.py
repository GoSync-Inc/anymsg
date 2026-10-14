"""AnyMsg's HTTP SDK; server and collectors remain operator-managed."""
from .client import AnyMsgError, Client

__version__ = "0.9.17"
__all__ = ["AnyMsgError", "Client", "__version__"]
