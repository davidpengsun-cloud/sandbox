"""Pack-role permissions shared by the command core and the store-backed job."""

from collections.abc import Iterable, Mapping


ACCESS_ADMIN = "access_admin"
ACCESS_ADMIN_LABEL = "Access admin"
PACK_CONTENT_ADMIN = "admin"
ADMIN_ROLES = frozenset({ACCESS_ADMIN, PACK_CONTENT_ADMIN})
ENGINEERING_PROCESSES = frozenset({"drafting", "building", "improving"})


CEREBRO_ROLES = ("business", "steward", "engineer", "admin")


def cerebro_role_of(role_id: str, role: Mapping | None) -> frozenset[str]:
    """The Cerebro roles (cerebro.md §2.1) one deployment role gives, from the processes its pack declares (§2.2, §8.1).

    The access administrator (the reserved `access_admin`, matrix row 9's check) and the pack's content `admin` role are
    Admin alone. Otherwise `certification`, or business acceptance (`accepts_drafts`: `accepting`, or writes to terms and
    mappings without certification), gives Steward, and `drafting`, `building` or `improving` gives Engineer. `querying`
    gives Business only to a role that holds none of those: every role answers questions, so querying alone marks the
    business user. `tagging` alone, or a role the release does not define, gives none.
    """
    if role_id in ADMIN_ROLES:
        return frozenset({"admin"})
    role = dict(role or {})
    processes = set(role.get("processes", []))
    governing = ({"steward"} if "certification" in processes or accepts_drafts(role) else set()) | (
        {"engineer"} if processes & ENGINEERING_PROCESSES else set())
    return frozenset(governing or ({"business"} if "querying" in processes else set()))


def cerebro_roles(roles: Iterable[str], definitions: Mapping[str, Mapping]) -> list[str]:
    """The Cerebro roles a person's deployment roles give (cerebro_role_of), each role read from definitions, the
    release's roles by id, in the matrix's order. A person holding several deployment roles holds every Cerebro role
    they give."""
    held = {cerebro for role in roles for cerebro in cerebro_role_of(role, definitions.get(role))}
    return [role for role in CEREBRO_ROLES if role in held]


CEREBRO_MATRIX = {
    "ask": ("Ask the agent", ("business", "steward", "engineer")),
    "graph": ("Read the graph", ("business", "steward", "engineer")),
    "feedback": ("Suggest a change or give feedback on answers", ("business", "steward", "engineer")),
    "curate": ("Curate: accept or reject proposals", ("steward",)),
    "review": ("Review candidates: business acceptance, steward certification", ("steward",)),
    "build": ("Build and onboard candidates", ("engineer",)),
    "release": ("Release / roll back a certified candidate that passed the checks", ("engineer",)),
    "retrieval": ("Read the retrieval-side APIs", ("engineer",)),
}


def matrix_allows(held: Iterable[str], row: str) -> bool:
    """Whether any of the Cerebro roles held has a tick in the matrix row (cerebro.md §2.1, §8.2)."""
    return bool(set(held) & set(CEREBRO_MATRIX[row][1]))


def check_pack_roles(roles: Iterable[str]) -> list[str]:
    """Refuse the reserved platform role without consulting deployment state."""
    return [f"{ACCESS_ADMIN} is reserved for access administration; a pack may not define it"] if ACCESS_ADMIN in roles else []


OPERATIONAL_PROCESSES = {
    "drafting": ("/onboard/tables", "/onboard/source", "/onboard/rows", "/onboard/draft"),
    "building": ("/onboard/run", "/onboard/margin"),
    "improving": ("/onboard/run",),
    "accepting": ("/onboard/accept",),
}


def process_vocabulary(constants: dict[str, str] | None = None) -> dict[str, str]:
    """The model's processes plus platform operations, preserving model order and descriptions."""
    out = dict(constants or {})
    for process, routes in OPERATIONAL_PROCESSES.items():
        out.setdefault(process, f"Platform {process}: {', '.join(routes)}")
    return out


def accepts_drafts(role: dict) -> bool:
    """Explicit acceptance, or the existing business role's writes without certification."""
    return "accepting" in role.get("processes", []) or (
        {"terms", "mappings"} <= set(role.get("writes", [])) and "certification" not in role.get("processes", []))


def has_process(role: dict, process: str) -> bool:
    return process in role.get("processes", [])


def may_improve_unit(role: dict, unit: str) -> bool:
    """An improvement process grant is bounded by the role's readable units."""
    reads = role.get("reads", [])
    return has_process(role, "improving") and (reads == "all" or unit in reads)


def permissions(role: dict, writable_: bool = True) -> dict[str, bool]:
    """Actions allowed by a role, independent of where that role was loaded. Separation of duties (cerebro.md §2.1):
    a role that certifies signs off; a role that builds releases and rolls back. Business acceptance is the steward's
    (matrix row 5, §8.4 change 5): a role that accepts drafts, or one that certifies, accepts; certifying stays the
    `certification` process's alone, and one person never certifies their own acceptance (kge/estate/onboard/review.py)."""
    steward = has_process(role, "certification")
    business = accepts_drafts(role) or steward
    drafting = has_process(role, "drafting")
    building = has_process(role, "building")
    improving = has_process(role, "improving")
    return {"read": business or drafting, "create": writable_ and drafting, "draft": writable_ and drafting,
            "accept": writable_ and business, "certify": writable_ and steward,
            "run": writable_ and building, "improve": writable_ and improving,
            "participant": business or steward or drafting or building or improving,
            "sign_off": writable_ and steward, "release": writable_ and building}
