"""Pack-role permissions shared by the command core and the store-backed job."""


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
    """Actions allowed by a role, independent of where that role was loaded."""
    business = accepts_drafts(role)
    steward = has_process(role, "certification")
    drafting = has_process(role, "drafting")
    building = has_process(role, "building")
    improving = has_process(role, "improving")
    return {"read": business or drafting, "create": writable_ and drafting, "draft": writable_ and drafting,
            "accept": writable_ and business, "certify": writable_ and steward,
            "run": writable_ and building, "improve": writable_ and improving,
            "participant": business or steward or drafting or building or improving,
            "sign_off": writable_ and steward, "release": writable_ and steward}
