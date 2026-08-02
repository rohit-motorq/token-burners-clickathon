"""Langfuse tracing (SDK v4, OTel-based — a different API from the old
`langfuse.decorators` module, see INNER_CONTEXT.md). The v4 client degrades
gracefully on its own (logs one warning, doesn't raise) when
LANGFUSE_PUBLIC_KEY/LANGFUSE_SECRET_KEY aren't set, so no custom no-op shim
is needed here — just re-export the real API and let it self-disable."""
from langfuse import get_client, observe, propagate_attributes

from . import config

# lets callers skip get_client()/flush() entirely when unconfigured, instead
# of relying on the SDK's own per-call "client initialized without public_key"
# warning as the only signal.
enabled = bool(config.LANGFUSE_PUBLIC_KEY and config.LANGFUSE_SECRET_KEY)

__all__ = ["observe", "get_client", "propagate_attributes", "enabled"]
