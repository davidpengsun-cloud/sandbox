"""Plain failures for warehouse operations on the answering path."""

WAREHOUSE_PROBLEM = "The requested data could not be read. Please try again or contact the data steward."


def warehouse_guard(guard: dict) -> dict:
    """Keep static SQL-policy explanations; replace warehouse diagnostics from a dry run."""
    if not guard.get("passed") and guard.get("failed_at") == "dry run":
        return {**guard, "problems": ["dry run failed: " + WAREHOUSE_PROBLEM]}
    return guard
