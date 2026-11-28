"""The safe identifier and agent vocabulary for question telemetry."""
import re
from datetime import datetime, timezone

AGENTS = frozenset({"kge_http", "kge_mcp", "context_ask", "context_playground", "discover_ask", "cerebro"})
HEX_TRACE = re.compile(r"[0-9a-f]{32}\Z")
HEX_SPAN = re.compile(r"[0-9a-f]{16}\Z")
SESSION = re.compile(r"(?:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|conv-[0-9]{1,20})\Z")
SPAN_NAME = re.compile(r"(?:kge(?:\.[a-z_]+)+|[A-Z]{3,7}(?: /[A-Za-z0-9_/{}:.-]{0,120})?|span)\Z")


def instant(value: str) -> datetime:
    """Parse an ISO timestamp, treating date-only values as UTC midnight."""
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return (parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
