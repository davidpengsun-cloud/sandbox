"""Access changes are live, audited, role-scoped and bounded by the reader TTL."""
import json
from types import SimpleNamespace

import pytest
import yaml

from kge.adapters import appstate
from kge.adapters.appstate import bigquery
from kge.pipeline import access_table as table
from kge.pipeline.profile import Profile
from kge.pipeline.settings import ROOT
from tests import bigquery_fake

ROLES = {"reader": {"id": "reader", "reads": ["retail"], "processes": ["querying"]},
         "keeper": {"id": "keeper", "reads": ["wealth"], "writes": ["terms"], "processes": ["certification"]},
         "admin": {"id": "admin", "reads": "all", "processes": ["certification"]}}
ADMIN = "admin@example.com"


@pytest.fixture(params=["files", "bigquery"])
def prof(tmp_path, monkeypatch, request):
    monkeypatch.setenv("APP_STATE_BACKEND", request.param)
    monkeypatch.setenv("ACCESS_ADMINS", f" {ADMIN.upper()}, bootstrap@example.com ")
    monkeypatch.delenv("STORE_URL", raising=False)
    if request.param == "bigquery":
        monkeypatch.setenv("BIGQUERY_PROJECT", bigquery_fake.PROJECT)
        monkeypatch.setenv("BIGQUERY_DATASET_PREFIX", "kge")
        fake = bigquery_fake.FakeBigQuery()
        monkeypatch.setattr(bigquery, "_ENSURED", set())
        monkeypatch.setattr(bigquery.BigQueryState, "_post", lambda self, body: fake.state_post(body))
    prof = Profile(id="acme", client="acme", clients=tmp_path / "clients", artifacts=tmp_path / "artifacts",
                   store=tmp_path / "store", landing=tmp_path / "landing", warehouses=tmp_path / "warehouses",
                   identities=tmp_path / "acme" / "identities.yaml", baseline=None, registry=tmp_path / "profiles.yaml")
    pack = prof.clients / prof.client / "pack.yaml"
    pack.parent.mkdir(parents=True)
    pack.write_text(yaml.safe_dump({"access": {"roles": list(ROLES.values())}}))
    folder = prof.artifacts / prof.client / "a1" / "access"
    folder.mkdir(parents=True)
    (folder / "roles.json").write_text(json.dumps({"roles": list(ROLES.values())}))
    (folder.parent.parent / "current").symlink_to("a1")
    prof.identities.parent.mkdir(parents=True, exist_ok=True)
    return prof


def grant(prof, username="person@example.com", roles=None):
    return table.grant(prof, username, roles or ["reader"], "Person", by=ADMIN, via="api")


def test_union_names_and_admins_do_not_inherit_pack_admin(prof):
    grant(prof, roles=["reader", "keeper"])
    table.set_admins(prof, ["operator@example.com"], by="operator@example.com")
    reader = table.Access.for_profile(prof)
    assert reader.roles_of("PERSON@example.com") == {"reader", "keeper"}
    assert reader.role_of_person("person@example.com", ROLES) == {
        "reads": ["retail", "wealth"], "writes": ["terms"], "processes": ["certification", "querying"]}
    assert reader.name_of("person@example.com") == "Person"
    assert reader.name_of(ADMIN) == ADMIN
    assert reader.admins() == {ADMIN, "bootstrap@example.com", "operator@example.com"}
    assert reader.roles_of(ADMIN) == {"access_admin"}
    assert reader.permissions(ADMIN, ROLES)["certify"] is False
    assert reader.is_admin(ADMIN) and reader.has_process("person@example.com", "querying", ROLES)
    assert reader.readable_units("person@example.com", ROLES) == ["retail", "wealth"]
    assert reader.people() == {"person@example.com", ADMIN, "bootstrap@example.com", "operator@example.com"}
    assert reader.role_of_person("person@example.com", {"reader": {"reads": "all"}})["reads"] == "all"
    assert reader.role_of_person("missing", ROLES)["processes"] == []


@pytest.mark.parametrize("roles,username,match", [(["access_admin"], "p@example.com", "reserved"),
    (["unknown"], "p@example.com", "unknown role"), ([], "p@example.com", "non-empty"),
    (["reader"], "bad name", "username"), (["reader"], "role:reader", "username")])
def test_refusals_write_nothing(prof, roles, username, match):
    with pytest.raises(ValueError, match=match):
        table.grant(prof, username, roles, "Person", by=ADMIN, via="api")
    assert appstate.access_state(prof).access_audit() == []


@pytest.mark.parametrize("via", ["api", "job"])
def test_only_access_admins_change_regular_grants_and_reserved_role_is_refused(prof, via):
    with pytest.raises(PermissionError, match="not an admin"):
        table.grant(prof, "a@example.com", ["reader"], "A", by="stranger@example.com", via=via)
    with pytest.raises(PermissionError):
        table.revoke(prof, "a@example.com", by="stranger@example.com", via=via)
    with pytest.raises(ValueError, match="reserved"):
        table.grant(prof, "a@example.com", ["access_admin"], "A", by=ADMIN, via=via)


def test_admin_changes_are_operator_audited_and_preserve_business_roles(prof):
    grant(prof)
    entries = table.set_admins(prof, "person@example.com,operator@example.com", by="op@example.com")
    assert all(entry["via"] == "job" and entry["by"] == "op@example.com" for entry in entries)
    assert table.set_admins(prof, "person@example.com operator@example.com", by="op@example.com") == []
    grant(prof, roles=["keeper"])
    assert table.Access.for_profile(prof).roles_of("person@example.com") == {"access_admin", "keeper"}
    table.revoke(prof, "person@example.com", by=ADMIN, via="api")
    assert table.Access.for_profile(prof).roles_of("person@example.com") == {"access_admin"}
    grant(prof, roles=["reader"])
    table.set_admins(prof, [], by="op@example.com")
    assert table.Access.for_profile(prof).roles_of("person@example.com") == {"reader"}
    assert "operator@example.com" not in table.Access.for_profile(prof).people()
    assert ADMIN in table.admins(prof)


@pytest.mark.parametrize("emails,by", [("not-email", "op@example.com"), ("a@example.com", ""),
                                         ("a@example.com", "principal")])
def test_admin_inputs_are_validated_before_any_write(prof, emails, by):
    with pytest.raises(ValueError):
        table.set_admins(prof, emails, by)
    assert appstate.access_state(prof).access_audit() == []


def test_revocation_and_display_changes_visible_at_exact_ttl_and_copies_are_isolated(prof):
    grant(prof)
    clock = SimpleNamespace(now=0)
    reader = table.Access.for_profile(prof, clock=lambda: clock.now)
    assert reader.roles_of("person@example.com") == {"reader"}
    reader.grants()[0]["roles"].append("keeper")
    assert reader.roles_of("person@example.com") == {"reader"}
    table.revoke(prof, "person@example.com", by=ADMIN, via="job")
    clock.now = 59.99
    assert "person@example.com" in reader.people()
    clock.now = 60
    assert reader.roles_of("person@example.com") == set()
    assert "person@example.com" not in reader.people()
    assert not table.changed_outside(prof)


def test_reader_fetches_once_per_ttl_and_retries_failures():
    calls = []
    clock = SimpleNamespace(now=0)
    def rows():
        calls.append(clock.now)
        if len(calls) == 2:
            raise RuntimeError("temporarily unavailable")
        return []
    reader = table.Access(SimpleNamespace(access_grants=rows), clock=lambda: clock.now)
    reader.grants()
    reader.grants()
    assert calls == [0]
    clock.now = 60
    with pytest.raises(RuntimeError):
        reader.grants()
    reader.grants()
    assert calls == [0, 60, 60]


def test_migration_merges_roles_keeps_ids_maps_emails_and_preserves_pack_admin(prof):
    seed = yaml.safe_dump({"asker": "ann", "principals": [
        {"principal": "ann", "role": "reader"}, {"principal": "ann", "role": "keeper"},
        {"principal": "sam", "role": "reader"}, {"principal": "root", "role": "admin"}]}).encode()
    result = table.migrate(prof, seed, "ann=ann@example.com", by=ADMIN)
    assert result == {"migrated": True, "rows": 3, "asker": "reader", "missing_emails": ["root", "sam"],
                      "written": [{"username": "ann@example.com", "name": "ann", "roles": ["keeper", "reader"]},
                                  {"username": "sam", "name": "sam", "roles": ["reader"]},
                                  {"username": "root", "name": "root", "roles": ["admin"]}]}
    reader = table.Access.for_profile(prof)
    assert reader.roles_of("ann@example.com") == {"reader", "keeper"}
    assert reader.roles_of("sam") == {"reader"}
    assert "root" not in reader.admins()
    assert reader.roles_of("root") == {"admin"}
    assert reader.readable_units("root", ROLES) == "all"
    assert all(e["via"] == "migration" and e["by"] == ADMIN for e in appstate.access_state(prof).access_audit())
    before = appstate.access_state(prof).access_audit()
    assert table.migrate(prof, seed)["migrated"] is False
    assert appstate.access_state(prof).access_audit() == before
    for username in ("ann@example.com", "sam", "root"):
        table.revoke(prof, username, by=ADMIN, via="job")
    assert table.migrate(prof, seed)["migrated"] is False
    assert table.Access.for_profile(prof).grants() == []


def test_seed_reads_deployment_only_once(prof):
    prof.identities.write_text("principals: [{principal: ann, role: reader}]\nasker: ann\n")
    assert table.seed(prof)["asker"] == "reader"
    prof.identities.write_text("principals: [{principal: intruder, role: keeper}]\n")
    assert not table.seed(prof)["migrated"]
    assert table.Access.for_profile(prof).roles_of("intruder") == set()


@pytest.mark.parametrize("seed,emails", [(b"principals: null", {}),
    (b"principals: [{principal: ann, role: reader}, {principal: 'bad id', role: keeper}]", {}),
    (b"principals: [{principal: ann, role: reader}]", "unknown=x@example.com"),
    (b"principals: []", "bad"), (b"principals: []", "ann=a@example.com ann=b@example.com")])
def test_bad_migration_writes_nothing(prof, seed, emails):
    with pytest.raises(ValueError):
        table.migrate(prof, seed, emails)
    assert appstate.access_state(prof).access_audit() == []


@pytest.mark.parametrize("rows,audit,digest,expected", [([], [], "empty", False),
    ([{}], [], "live", True), ([], [{"digest": "old", "outside": False}], "live", True),
    ([], [{"digest": "live", "outside": True}], "live", True),
    ([], [{"digest": "live", "outside": False}], "live", False)])
def test_outside_report_checks_live_digest_and_last_outside_flag(monkeypatch, rows, audit, digest, expected):
    state = SimpleNamespace(access_grants=lambda: rows, access_audit=lambda limit: audit, access_digest=lambda: digest)
    monkeypatch.setattr(table, "access_state", lambda prof: state)
    assert table.changed_outside(None) is expected


def test_authorization_and_mutation_share_the_same_revision(prof, monkeypatch):
    state = appstate.access_state(prof)
    original = state.access_put
    def raced(row, **kwargs):
        appstate.access_state(prof).access_put({"username": "other", "roles": ["reader"]}, by=ADMIN, via="job")
        return original(row, **kwargs)
    monkeypatch.setattr(state, "access_put", raced)
    monkeypatch.setattr(table, "access_state", lambda prof: state)
    with pytest.raises(appstate.StateConflict):
        grant(prof)
    assert [row["username"] for row in appstate.access_state(prof).access_grants()] == ["other"]


def test_multirow_admin_change_refuses_concurrent_updates(prof, monkeypatch):
    state = appstate.access_state(prof)
    original = state.access_put
    def raced(row, **kwargs):
        entry = original(row, **kwargs)
        appstate.access_state(prof).access_put({"username": "z@example.com", "roles": ["reader"]}, by=ADMIN, via="job")
        return entry
    monkeypatch.setattr(state, "access_put", raced)
    monkeypatch.setattr(table, "access_state", lambda prof: state)
    with pytest.raises(appstate.StateConflict):
        table.set_admins(prof, ["a@example.com", "z@example.com"], by="op@example.com")
    assert "access_admin" not in next(row for row in state.access_grants() if row["username"] == "z@example.com")["roles"]


def test_seed_reads_store_deployment(prof, monkeypatch, tmp_path):
    from kge.adapters.objstore import open_store
    monkeypatch.setenv("STORE_URL", str(tmp_path / "objects"))
    store = open_store(str(tmp_path / "objects"))
    store.put("clients/acme/pack.yaml", (prof.clients / prof.client / "pack.yaml").read_bytes())
    store.put("deploy/acme/identities.yaml", b"principals: [{principal: ann, role: reader}]\nasker: ann\n")
    assert table.seed(prof)["asker"] == "reader"
    assert table.Access.for_profile(prof).roles_of("ann") == {"reader"}


def test_pack_admin_is_grantable_and_never_grants_access_administration(prof):
    grant(prof, roles=["admin"])
    reader = table.Access.for_profile(prof)
    assert reader.roles_of("person@example.com") == {"admin"}
    assert reader.permissions("person@example.com", ROLES)["certify"]
    assert reader.readable_units("person@example.com", ROLES) == "all"
    assert not reader.is_admin("person@example.com")
    table.set_admins(prof, ["person@example.com"], by=ADMIN)
    assert table.Access.for_profile(prof).roles_of("person@example.com") == {"admin", "access_admin"}
    table.set_admins(prof, [], by=ADMIN)
    assert table.Access.for_profile(prof).roles_of("person@example.com") == {"admin"}
    table.revoke(prof, "person@example.com", by=ADMIN, via="api")
    assert not table.Access.for_profile(prof).roles_of("person@example.com")


def test_pack_reserved_role_refusal_and_display_label():
    assert table.check_pack_roles(ROLES) == []
    assert table.check_pack_roles(["reader", "access_admin"]) == [
        "access_admin is reserved for access administration; a pack may not define it"]
    assert table.ACCESS_ADMIN_LABEL == "Access admin"


@pytest.mark.parametrize("via", ["api", "job"])
def test_free_form_username_can_be_granted_and_regranted(prof, via):
    assert not table.check_grant("legacy", ["reader"], ROLES)
    table.grant(prof, "legacy", ["reader"], "Legacy", by=ADMIN, via=via)
    table.revoke(prof, "legacy", by=ADMIN, via=via)
    table.grant(prof, "legacy", ["keeper"], "Legacy", by=ADMIN, via=via)
    assert table.Access.for_profile(prof).roles_of("legacy") == {"keeper"}


def test_migration_asker_chooses_querying_binding_instead_of_first(prof):
    result = table.migrate(prof, b"asker: ann\nprincipals: [{principal: ann, role: keeper}, {principal: ann, role: reader}]")
    assert result["asker"] == "reader"
    assert result["missing_emails"] == ["ann"]
    assert result["written"] == [{"username": "ann", "name": "ann", "roles": ["keeper", "reader"]}]
    again = table.migrate(prof, b"invalid")
    assert again["written"] == [] and again["missing_emails"] == ["ann"]


@pytest.mark.parametrize("role", ["keeper", "unknown", "access_admin"])
def test_migration_refuses_unusable_asker_before_writes(prof, role):
    raw = yaml.safe_dump({"asker": "ann", "principals": [{"principal": "ann", "role": role}]}).encode()
    with pytest.raises(ValueError, match="querying role|reserved|unknown role"):
        table.migrate(prof, raw)
    assert appstate.access_state(prof).access_grants() == []
    assert appstate.access_state(prof).access_audit() == []


def test_module_helpers_share_one_reader_and_ttl(prof, monkeypatch):
    grant(prof)
    clock = SimpleNamespace(now=0)
    state = appstate.access_state(prof)
    original = state.access_grants
    calls = []
    def rows():
        calls.append(clock.now)
        return original()
    monkeypatch.setattr(state, "access_grants", rows)
    reader = table.Access(state, clock=lambda: clock.now)
    for _ in range(2):
        assert ADMIN in table.admins(prof, reader=reader)
        assert table.is_admin(prof, ADMIN, reader=reader)
        assert table.role_of_person(prof, "person@example.com", ROLES, reader=reader)["reads"] == ["retail"]
        assert not table.permissions(prof, "person@example.com", ROLES, reader=reader)["sign_off"]
        assert table.has_process(prof, "person@example.com", "querying", ROLES, reader=reader)
        assert table.readable_units(prof, "person@example.com", ROLES, reader=reader) == ["retail"]
    assert calls == [0]
    table.revoke(prof, "person@example.com", by=ADMIN, via="api")
    clock.now = 60
    assert not table.has_process(prof, "person@example.com", "querying", ROLES, reader=reader)
    assert calls == [0, 60]


def test_migration_preserves_every_sample_binding_including_pack_admin(prof):
    pack = yaml.safe_load((ROOT / "clients/meridian/pack.yaml").read_bytes())
    (prof.clients / prof.client / "pack.yaml").write_text(yaml.safe_dump(pack))
    seed = yaml.safe_load((ROOT / "deploy/meridian/identities.yaml").read_bytes())
    seed["client"] = prof.client
    raw = yaml.safe_dump(seed).encode()
    bindings = seed["principals"]
    result = table.migrate(prof, raw)
    reader = table.Access.for_profile(prof)
    for binding in bindings:
        assert binding["role"] in reader.roles_of(binding["principal"])
    templates = {role["id"]: role for role in pack["access"]["roles"]}
    assert reader.role_of_person("admin.meridian@example.com", templates) == {
        field: templates["admin"][field] if templates["admin"][field] == "all" else sorted(templates["admin"][field])
        for field in ("reads", "writes", "processes")}
    assert not reader.is_admin("admin.meridian@example.com")
    assert table.querying_role(result["asker"], templates)
    assert result["rows"] == len({binding["principal"] for binding in bindings})
    assert result["missing_emails"] == []


@pytest.mark.parametrize("bad", ["", "a b", "a\tb", "a\nb", "name\n", "a\x00b", "a\u200bb", "x" * 255,
                                    "access_admin", "ＲＯＬＥ:reader"])
def test_username_guardrails_refuse_before_mutation(prof, bad):
    with pytest.raises(ValueError, match="username"):
        grant(prof, bad)
    assert appstate.access_state(prof).access_audit() == []


@pytest.mark.parametrize("name", [" Reader ", "ＲＥＡＤＥＲ", "reader"])
def test_username_normalization_uniqueness_and_free_form_role_collision(prof, name):
    grant(prof, "reader")
    grant(prof, name, ["keeper"])
    rows = appstate.access_state(prof).access_grants()
    assert len(rows) == 1 and rows[0]["username"] == "reader"
    assert rows[0]["roles"] == ["keeper"]
    assert table.Access.for_profile(prof).roles_of(name) == {"keeper"}


def test_username_can_equal_an_ordinary_pack_role(prof):
    grant(prof, "admin", ["reader"])
    assert table.Access.for_profile(prof).roles_of("admin") == {"reader"}
    assert not table.Access.for_profile(prof).is_admin("admin")


@pytest.mark.parametrize("vector", json.loads((ROOT / "portal/server/test/username-vectors.json").read_text())["vectors"],
                         ids=lambda vector: vector["why"])
def test_username_matches_shared_portal_vectors(vector):
    if vector["expected"] is None:
        with pytest.raises(ValueError, match="username"):
            table.normalize_username(vector["input"])
        assert table.check_username(vector["input"])
    else:
        assert table.normalize_username(vector["input"]) == vector["expected"]
        assert table.check_username(vector["input"]) == []


def test_portal_unassigned_ranges_match_python_unicode():
    import sys
    import unicodedata

    shipped = json.loads((ROOT / "portal/server/src/unassigned.json").read_text())
    assert shipped["unicode_version"] == "15.0.0", "the table is the image's Python 3.12 Unicode"
    if sys.version_info[:2] != (3, 12):
        pytest.skip("the table is generated by the image's Python 3.12")
    ranges = []
    for point in range(0x110000):
        if unicodedata.category(chr(point)) != "Cn":
            continue
        if ranges and ranges[-1][1] == point - 1:
            ranges[-1][1] = point
        else:
            ranges.append([point, point])
    assert shipped == {"unicode_version": unicodedata.unidata_version, "ranges": ranges}


def test_kge_unassigned_table_matches_portal_bytes():
    assert (ROOT / "kge/pipeline/unassigned.json").read_bytes() == (
        ROOT / "portal/server/src/unassigned.json").read_bytes()


@pytest.mark.parametrize("runtime_category", [None, "So"])
def test_username_refuses_unicode_15_1_addition(monkeypatch, runtime_category):
    if runtime_category is not None:
        category = table.unicodedata.category
        monkeypatch.setattr(table.unicodedata, "category", lambda c: runtime_category if c == "⿼" else category(c))
    with pytest.raises(ValueError, match="unassigned"):
        table.normalize_username("ab⿼")


def test_username_still_refuses_runtime_unassigned_characters(monkeypatch):
    category = table.unicodedata.category
    monkeypatch.setattr(table.unicodedata, "category", lambda c: "Cn" if c == "a" else category(c))
    with pytest.raises(ValueError, match="unassigned"):
        table.normalize_username("analyst.meridian@example.com")


@pytest.mark.parametrize("bad", ["operator@example.com\n", "operator\u200b@example.com", "role:operator@example.com"])
def test_admin_and_migration_writes_refuse_invalid_usernames_before_mutation(prof, bad):
    with pytest.raises(ValueError):
        table.set_admins(prof, ["valid@example.com", bad], by=ADMIN)
    raw = yaml.safe_dump({"principals": [{"principal": "old", "role": "reader"}]}).encode()
    with pytest.raises(ValueError):
        table.migrate(prof, raw, {"old": bad})
    assert appstate.access_state(prof).access_audit() == []


def test_admin_migration_and_revoke_share_normalized_names_and_audit(prof):
    raw = yaml.safe_dump({"principals": [{"principal": "old", "role": "reader"}]}).encode()
    table.migrate(prof, raw, {"old": "ＳＴＲＡＳＳＥ@EXAMPLE.COM"}, by=" ＯＰ@EXAMPLE.COM ")
    table.set_admins(prof, ["Straße@Example.com"], by=" ＯＰ@EXAMPLE.COM ")
    rows = appstate.access_state(prof).access_grants()
    assert len(rows) == 1 and rows[0]["username"] == "strasse@example.com"
    table.revoke(prof, "ＳＴＲＡＳＳＥ@EXAMPLE.COM", by=" STRASSE@EXAMPLE.COM ", via="api")
    audit = appstate.access_state(prof).access_audit()
    assert {entry["username"] for entry in audit} == {"strasse@example.com"}
    assert [entry["by"] for entry in audit] == ["strasse@example.com", "op@example.com", "op@example.com"]


@pytest.mark.parametrize("client,role,match", [("other", "reader", "client 'other'"),
                                              ("acme", "missing_role", "unknown role 'missing_role'"),
                                              ("acme", "access_admin", "reserved")])
def test_seed_validates_client_and_every_role_without_an_asker_before_writes(prof, client, role, match):
    raw = yaml.safe_dump({"client": client, "principals": [
        {"principal": "valid@example.com", "role": "reader"},
        {"principal": "other@example.com", "role": role}]}).encode()
    with pytest.raises(ValueError, match=match):
        table.migrate(prof, raw)
    state = appstate.access_state(prof)
    assert state.access_grants() == [] and state.access_audit() == []


def test_seed_uses_the_callers_role_snapshot_without_reading_mutable_pack(prof):
    (prof.clients / prof.client / "pack.yaml").unlink()
    raw = yaml.safe_dump({"client": prof.client, "asker": "person@example.com", "principals": [
        {"principal": "person@example.com", "role": "candidate_reader"}]}).encode()
    templates = {"candidate_reader": {"id": "candidate_reader", "processes": ["querying"], "reads": ["retail"]}}
    result = table.migrate(prof, raw, roles=templates)
    assert result["asker"] == "candidate_reader"
    assert table.Access.for_profile(prof).roles_of("person@example.com") == {"candidate_reader"}


def test_seed_refuses_a_role_missing_from_the_callers_snapshot_even_if_live_pack_has_it(prof):
    raw = yaml.safe_dump({"client": prof.client, "principals": [{"principal": "person@example.com", "role": "reader"}]}).encode()
    with pytest.raises(ValueError, match="unknown role 'reader'"):
        table.migrate(prof, raw, roles={"keeper": ROLES["keeper"]})
    state = appstate.access_state(prof)
    assert state.access_grants() == [] and state.access_audit() == []


def test_initialized_access_does_not_revalidate_or_read_a_replaced_seed_pack(prof):
    grant(prof)
    before = appstate.access_state(prof).access_audit()
    (prof.clients / prof.client / "pack.yaml").unlink()
    raw = yaml.safe_dump({"client": "other", "principals": [{"principal": "wrong@example.com", "role": "unknown"}]}).encode()
    assert table.migrate(prof, raw)["migrated"] is False
    assert appstate.access_state(prof).access_audit() == before



def test_bootstrap_skips_invalid_words_without_disclosing_them(prof, monkeypatch, caplog):
    monkeypatch.setenv("ACCESS_ADMINS", " Good@example.com, role:hidden-secret, bad word, access_admin, ")
    assert table.Access.for_profile(prof).people() == {"good@example.com"}
    assert [entry.message for entry in caplog.records] == ["ACCESS_ADMINS: skipped 3 invalid entries"]


def test_migration_uses_one_atomic_port_operation(prof, monkeypatch):
    state = appstate.access_state(prof)
    original = state.access_migrate
    calls = []
    def batch(rows, **kwargs):
        calls.append(rows)
        return original(rows, **kwargs)
    monkeypatch.setattr(state, "access_migrate", batch)
    monkeypatch.setattr(table, "access_state", lambda prof: state)
    raw = yaml.safe_dump({"principals": [{"principal": f"reader{i}@example.com", "role": "reader"}
                                        for i in range(3)]}).encode()
    assert table.migrate(prof, raw)["rows"] == 3
    assert len(calls) == 1 and len(calls[0]) == 3


def test_migration_middle_row_error_leaves_no_partial_seed(prof, monkeypatch):
    from kge.adapters.appstate import files

    def inject(module):
        original = module._change
        def failing(*args, **kwargs):
            if args[3] == "second@example.com":
                raise RuntimeError("middle row fault")
            return original(*args, **kwargs)
        monkeypatch.setattr(module, "_change", failing)
    inject(files)
    inject(bigquery)
    raw = yaml.safe_dump({"principals": [{"principal": name, "role": "reader"}
                                        for name in ["first@example.com", "second@example.com", "third@example.com"]]}).encode()
    with pytest.raises(RuntimeError, match="middle row fault"):
        table.migrate(prof, raw)
    state = appstate.access_state(prof)
    assert state.access_grants() == [] and state.access_audit() == []
