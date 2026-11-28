"""A node file whose first quoted field comes after the lines Ladybug samples to sniff the CSV dialect still loads:
COPY names the quote character, so a comma inside a quoted field late in a file is part of the field, not a separator."""
from __future__ import annotations

from kge.adapters.graphstore.ladybug import LadybugStore

SAMPLED_LINES = 300


def test_a_quoted_field_after_the_sampled_lines_loads(tmp_path):
    store = LadybugStore()
    plain = [f"{i},plain {i}" for i in range(SAMPLED_LINES + 1)]
    late = SAMPLED_LINES + 1
    rows = tmp_path / "Thing.csv"
    rows.write_text("\n".join(["id,name", *plain, f'{late},"Smith, Jones and Co"']) + "\n")
    db = store.open(tmp_path / "graph.ladybug")
    with store.connect(db) as conn:
        store.create(conn, {}, ["CREATE NODE TABLE Thing(id INT64, name STRING, PRIMARY KEY(id))"])
        with store.batch(conn) as writer:
            writer.load_file("Thing", rows)
        q = store.q(conn)
        assert q("MATCH (t:Thing) RETURN count(t) AS n")[0]["n"] == late + 1
        assert q("MATCH (t:Thing) WHERE t.id = $k RETURN t.name AS name", {"k": late})[0]["name"] == "Smith, Jones and Co"
    db.close()
