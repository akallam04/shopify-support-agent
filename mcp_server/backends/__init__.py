"""Store backends behind one contract: live Shopify Admin API, or the simulated JSON store."""

from mcp_server.backends.base import FoundOrder, StoreBackend

__all__ = ["FoundOrder", "StoreBackend"]
