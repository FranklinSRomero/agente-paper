"""Discord channel adapter placeholder.

Design notes:
- Inbound: message_create events -> normalize to IncomingMessage.
- Platform status: trigger typing indicator during long operations.
- Outbound: send message or attachment response.
"""

from .base import IncomingMessage


def normalize_inbound_event(payload: dict) -> IncomingMessage | None:
    # Pending implementation when Discord gateway/webhook integration is added.
    _ = payload
    return None
