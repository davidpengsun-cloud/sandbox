"""/whoami states a person's Cerebro roles, derived in one place (kge.pipeline.access) from the processes each of their
deployment roles declares in the pack, as cerebro.md §2.2 and §8.1 state: certification or business acceptance gives
Steward, drafting, building or improving gives Engineer, querying alone gives Business, the access administrator and the
pack's admin role give Admin; several deployment roles give every Cerebro role they map to; pipeline_tagger gives none.
Every client's pack is read as it is: Meridian's named roles give exactly the Cerebro roles issue #466 lists."""
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from kge.estate.onboard.clients import _client_pack
from kge.pipeline.access import CEREBRO_ROLES, cerebro_roles
from kge.services.identity import service

WHO = "person@example.com"
CLIENTS = Path(__file__).resolve().parents[2] / "clients"


def pack_roles(client: str) -> dict:
    pack = yaml.safe_load((CLIENTS / client / "pack.yaml").read_text())
    return {role["id"]: role for role in pack["access"]["roles"]}


MERIDIAN = pack_roles("meridian")
ISSUE_466 = {
    "analyst_retail": ["business"], "analyst_enterprise": ["business"], "advisor_wealth": ["business"],
    "finance_reporting": ["business"],
    "steward": ["steward"], "steward_glossary": ["steward"], "business_team": ["steward"],
    "knowledge_engineer": ["engineer"],
    "admin": ["admin"],
    "pipeline_tagger": [],
}
EXPECTED = {
    "thornbury": {"actuary": ["business"], "claims_steward": ["steward"], "knowledge_engineer": ["engineer"],
                  "pipeline_tagger": []},
    "fellmoor": {"network_analyst": ["business"], "fares_steward": ["steward"], "knowledge_engineer": ["engineer"],
                 "pipeline_tagger": []},
}


def test_meridians_named_roles_give_exactly_the_cerebro_roles_issue_466_lists():
    assert set(MERIDIAN) == set(ISSUE_466)
    assert {role: cerebro_roles([role], MERIDIAN) for role in MERIDIAN} == ISSUE_466


@pytest.mark.parametrize("client", sorted(EXPECTED))
def test_every_other_clients_roles_follow_their_processes(client):
    roles = pack_roles(client)
    assert set(roles) == set(EXPECTED[client])
    assert {role: cerebro_roles([role], roles) for role in roles} == EXPECTED[client]


def test_a_kge_client_made_packs_roles_follow_their_processes():
    roles = {role["id"]: role for role in _client_pack("acme", "Acme", [])["access"]["roles"]}
    assert {role: cerebro_roles([role], roles) for role in roles} == {
        "analyst": ["business"], "business_team": ["steward"], "knowledge_engineer": ["engineer"],
        "steward": ["steward"], "pipeline_tagger": []}


def test_the_cerebro_role_comes_from_the_processes_not_the_name():
    assert cerebro_roles(["steward"], {"steward": {"processes": ["querying"]}}) == ["business"]
    assert cerebro_roles(["anyone"], {"anyone": {"processes": ["certification", "querying"]}}) == ["steward"]
    assert cerebro_roles(["anyone"], {"anyone": {"processes": ["accepting", "querying"]}}) == ["steward"]
    assert cerebro_roles(["anyone"], {"anyone": {"processes": ["improving", "querying"]}}) == ["engineer"]
    assert cerebro_roles(["anyone"], {"anyone": {"processes": ["tagging"]}}) == []
    assert cerebro_roles(["anyone"], {"anyone": {}}) == []


@pytest.mark.parametrize("role", ["analyst_sme", "analyst", "unknown_role"])
def test_a_role_the_release_does_not_define_gives_none(role):
    assert cerebro_roles([role], MERIDIAN) == []


def test_the_access_administrator_is_admin_without_a_definition():
    assert cerebro_roles(["access_admin"], {}) == ["admin"]
    assert cerebro_roles(["access_admin"], MERIDIAN) == ["admin"]


def test_several_roles_give_every_cerebro_role_once_in_matrix_order():
    assert cerebro_roles(["admin", "knowledge_engineer", "steward", "analyst_retail", "steward_glossary",
                          "pipeline_tagger"], MERIDIAN) == ["business", "steward", "engineer", "admin"]
    assert cerebro_roles(["admin", "steward"], MERIDIAN) == ["steward", "admin"]
    assert cerebro_roles([], MERIDIAN) == []
    assert set(CEREBRO_ROLES) == {"business", "steward", "engineer", "admin"}


def _engine(row):
    return SimpleNamespace(g=SimpleNamespace(principal=lambda who: dict(row), role_templates=lambda: MERIDIAN))


def _people(roles, admin=False):
    return SimpleNamespace(access=SimpleNamespace(name_of=lambda _: "Person", roles_of=lambda _: set(roles),
                                                  is_admin=lambda _: admin))


@pytest.mark.parametrize("roles,admin,expected", [
    ({"analyst_retail"}, False, ["business"]),
    ({"steward", "knowledge_engineer"}, False, ["steward", "engineer"]),
    ({"admin", "steward_glossary", "finance_reporting"}, False, ["business", "steward", "admin"]),
    ({"pipeline_tagger"}, False, []),
    (set(), True, ["admin"]),
    (set(), False, []),
])
def test_whoami_from_the_access_table_carries_cerebro_roles(roles, admin, expected):
    body = service.whoami(_engine({"id": WHO}), WHO, _people(roles, admin))
    assert body["cerebro_roles"] == expected
    assert body["roles"] == sorted(roles)


def test_whoami_from_the_release_graph_maps_its_role_ids():
    row = {"id": WHO, "roles": [{"id": "steward", "label": "Steward"}, {"id": "analyst_retail", "label": "Analyst"}]}
    assert service.whoami(_engine(row), WHO)["cerebro_roles"] == ["business", "steward"]
    assert service.whoami(_engine({"id": WHO}), WHO)["cerebro_roles"] == []
