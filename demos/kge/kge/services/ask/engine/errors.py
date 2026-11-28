"""Plain failures for warehouse operations on the answering path."""
import re
from contextvars import ContextVar

WAREHOUSE_PROBLEM = "The requested data could not be read. Please try again or contact the data steward."
DRY_RUN_FAILED = "dry run failed: "
QUERY_FAULT = re.compile(
    r"syntax error|parser error|unrecognized name|binder error|referenced column|no matching signature|"
    r"conversion error|cannot coerce|could not cast|invalid cast|type mismatch|"
    r"not found: (?:column|field|name)|not found inside|unknown column", re.I)
"""A dry run's message that names a fault in the query itself, which a different plan can fix; any other (no access, a
missing table, an outage) is the warehouse's."""
_dry_run = ContextVar("kge_dry_run_problems", default=())


def warehouse_guard(guard: dict) -> dict:
    """Keep static SQL-policy explanations; replace warehouse diagnostics from a dry run. The warehouse's own words are
    kept for this request alone (dry_run_problems), never in the guard a reply carries."""
    if not guard.get("passed") and guard.get("failed_at") == "dry run":
        raw = tuple(str(p) for p in guard.get("problems") or ())
        if raw and not all(p.endswith(WAREHOUSE_PROBLEM) for p in raw):
            _dry_run.set(raw)
        return {**guard, "problems": [DRY_RUN_FAILED + WAREHOUSE_PROBLEM]}
    return guard


def forget_dry_run() -> None:
    """Forget the last dry run's words, before an attempt whose own dry run is to be read."""
    _dry_run.set(())


def dry_run_problems() -> tuple[str, ...]:
    """The warehouse's own words for the last dry run that failed in this request, since forget_dry_run: what a model
    is shown to correct its plan, and never a reply."""
    return _dry_run.get()


def query_fault(problems) -> bool:
    """Whether a dry run's words name a fault in the query itself (QUERY_FAULT), not the warehouse's."""
    return any(QUERY_FAULT.search(str(p)) for p in problems)
