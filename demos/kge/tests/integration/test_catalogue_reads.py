import json

import pytest

from kge.services.catalogue import get
from kge.services.data import service
from kge.services.data.engine import governance


PEOPLE = {"analyst_retail": (4, 10), "advisor_wealth": (6, 32), "steward": (10, 42),
          "analyst_enterprise": (18, 59), "business_team": (18, 59), "finance_reporting": (18, 59),
          "steward_glossary": (18, 59), "admin": (18, 59)}


@pytest.mark.parametrize("who", PEOPLE)
def test_every_landed_catalogue_product_can_be_previewed(eng, monkeypatch, who):
    catalogue = get(eng.profile, eng.g.readable_units(who))
    old_certified = set()
    for unit in eng.g.readable_units(who):
        rows = eng.g.q("MATCH (c:Col)-[m:MAPS_TO {certified: true}]->(t:Term) WHERE c.unit = $u RETURN DISTINCT c.tbl AS tbl", {"u": unit})
        rows += eng.g.q("MATCH (c:Context)-[:CERTIFIED_BY]->(g:Golden), (c)-[:USES_FACT]->(t:Tbl) WHERE t.unit = $u RETURN DISTINCT t.name AS tbl", {"u": unit})
        old_certified.update(f"{unit}.{row['tbl']}" for row in rows)
    current_rule = governance.landed_tables
    counts = []
    for membership in (lambda *args: old_certified, current_rule):
        monkeypatch.setattr(governance, "landed_tables", membership)
        allowed = 0
        for product in catalogue.products:
            try:
                service.preview(eng, who, product.unit_id, product.table)
            except service.DataRefused as exc:
                assert exc.status == 403 and "not landed" in exc.detail
            else:
                allowed += 1
        counts.append(allowed)
    assert tuple(counts) == PEOPLE[who]
    assert len(catalogue.products) == PEOPLE[who][1]
    print(json.dumps({"principal": who, "before": counts[0], "after": counts[1], "products": len(catalogue.products)}))


def test_retail_customers_and_wealth_flow_keep_their_own_scope(eng):
    assert service.query(eng, "analyst_retail", "retail", "SELECT count(*) AS n FROM customer").rows == [{"n": 200}]
    assert service.query(eng, "advisor_wealth", "wealth", "SELECT count(*) AS n FROM TRAN_FACT_1").rows == [{"n": 3945}]
    assert service.export(eng, "advisor_wealth", "wealth", "TRAN_FACT_1").row_count == 3945
    assert service.preview(eng, "advisor_wealth", "wealth", "MONTH_DIM_1").row_count > 0



def test_an_unlanded_product_keeps_catalogue_badges_and_refuses_every_read(eng, monkeypatch, tmp_path):
    from dataclasses import replace
    from kge.landing.zone import Zone
    from kge.pipeline.landed_tables import write_landed_tables
    original = next(p for p in get(eng.g.profile, ["retail"]).products if p.id == "retail.customer")
    profile = replace(eng.g.profile, landing=tmp_path)
    landed = Zone(profile.landing, profile.client).land_files(
        {"structured/retail.json": json.dumps({"source": "retail", "tables": [{"name": "account"}]}).encode()}, {})
    profile = replace(profile, store=tmp_path / "stores")
    write_landed_tables(profile.graph_path().parent, landed)
    monkeypatch.setattr(eng.g, "profile", profile)
    monkeypatch.setattr(eng.g, "release", landed.about)
    product = next(p for p in get(profile, ["retail"]).products if p.id == "retail.customer")
    assert product.badges == original.badges
    assert "certified" not in product.model_dump()
    for kind in ("preview", "export", "query"):
        argument = "SELECT customer_id FROM customer" if kind == "query" else "customer"
        with pytest.raises(service.DataRefused) as caught:
            getattr(service, kind)(eng, "analyst_retail", "retail", argument)
        assert caught.value.status == 403
        assert caught.value.detail == "The current release has not landed table(s): customer."
