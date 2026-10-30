"""The adapters: what reaches outside the process or picks a backend, one package per kind (kge/adapters/<kind>/), each
offering a protocol of kge/ports.py, its local and Google Cloud implementations side by side: appstate (AppState),
graphstore (GraphStore), model (ModelProvider, Embeddings), objstore (ObjectStore), retrieval (Retrieval) and warehouse
(WarehouseSet). A kind's package chooses its backend from the settings; callers import the package, never a backend's
module. bigquery.py is the BigQuery client the BigQuery backends share."""


SYNC_STEP = "kge.estate.load_bigquery"


def sync_step() -> str | None:
    """The step that copies a loaded graph to where the warehouses and retrieval read it, run after the pipeline and
    before the graph is marked loaded (make, kge/pipeline/profile.py --make's sync word; the improvement job's
    candidate): the BigQuery sync (kge/estate/load_bigquery.py) where the warehouses are BigQuery's (WAREHOUSE_BACKEND)
    or retrieval is (RETRIEVAL_BACKEND, else WAREHOUSE_BACKEND), as each kind's factory reads its setting; None where
    both read the graph and the estate's own files. The app state's backend plays no part: it is not copied."""
    from kge.adapters.retrieval import backend as retrieval
    from kge.adapters.warehouse import backend as warehouse
    return SYNC_STEP if "bigquery" in (warehouse(), retrieval()) else None


def building_candidate() -> bool:
    """Whether this process builds a candidate of the improvement job (candidate_settings), whose steps write only
    where nothing serves and copy no warehouse, which the served graph shares."""
    from kge.adapters.bigquery import graph_kind
    return graph_kind() == "candidates"


def candidate_settings() -> dict[str, str]:
    """The settings a candidate's steps run with (the improvement job's, kge/improve/abtest.py): its graph and chunks
    go to the client's candidates dataset (GRAPH_DATASET=candidates, kge/adapters/bigquery.py graph_kind), which nothing
    serves, never to the tables a release serves."""
    return {"GRAPH_DATASET": "candidates"}


def bigquery_preflight(client: str) -> str | None:
    """The project BigQuery resolves to, once the client's warehouses and graph datasets' tables are listed; None where
    this install keeps nothing in BigQuery."""
    from kge.pipeline.settings import gcp_native
    if not gcp_native():
        return None
    from kge.adapters.bigquery import BigQueryLoader, dataset_name
    bq = BigQueryLoader(client=client)
    _, project = bq.account()
    for kind in ("warehouses", "graph"):
        bq.tables(dataset_name(bq.dataset_prefix, client, kind))
    return project


def test_drive_tables(client: str) -> str:
    """The configured dataset prefix for the diagnostic's optional cleanup instructions."""
    from kge.adapters.bigquery import BigQueryLoader, dataset_name
    prefix = BigQueryLoader(client=client).dataset_prefix
    return dataset_name(prefix, client, "*")


def validate_settings() -> None:
    """Ask each backend's factory for the selection the settings make, so a contradiction is refused as the step that
    consumes it would refuse it, without opening a client's data."""
    from kge.adapters.appstate import selected
    from kge.adapters.graphstore import store
    from kge.adapters.retrieval import backend as retrieval_backend
    from kge.adapters.warehouse import backend as warehouse_backend
    warehouse_backend()
    retrieval_backend()
    store()
    selected()
