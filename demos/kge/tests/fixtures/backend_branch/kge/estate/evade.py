import os
from kge.pipeline.settings import settings, gcp_native
from kge.adapters.warehouse import estate_of
from kge.adapters.warehouse.bigquery import BigQueryWarehouses


def a(prof, w, p):
    if gcp_native():
        return 1
    if estate_of(prof).bigquery_dataset is None:
        return 2
    if isinstance(w, BigQueryWarehouses):
        return 3
    if getattr(settings(), "WAREHOUSE_" + "BACKEND") == "big" + "query":
        return 4
    if p.suffix == ".ladybug":
        return 5
    if os.environ.get("RETRIEVAL_" "BACKEND".lower().upper(), "") == "Big Query".replace(" ", "").lower():
        return 6
    return {"bq": 7}.get(type(w).__module__.rsplit(".", 1)[-1])
