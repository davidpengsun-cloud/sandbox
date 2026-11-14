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
    """The resolved project after dataset access and, when selected, actual graph creation are proved. The unique
    probe lives in the configured graph dataset, is awaited and removed before returning; local installs do nothing."""
    from kge.pipeline.settings import gcp_native
    if not gcp_native():
        return None
    from kge.adapters.bigquery import BigQueryLoader, dataset_name
    bq = BigQueryLoader(client=client)
    _, project = bq.account()
    for kind in ("warehouses", "graph"):
        bq.tables(dataset_name(bq.dataset_prefix, client, kind))
    from kge.adapters.graphstore import store_name
    if store_name() == "bigquery":
        from kge.adapters.bigquery import graph_kind
        graph_creation_preflight(bq, project, dataset_name(bq.dataset_prefix, client, graph_kind()))
    return project


def graph_creation_preflight(bq, project: str, dataset: str) -> None:
    """Prove CREATE PROPERTY GRAPH on the actual dataset, not metadata access or a script dry-run. Cleanup is
    child before parent after create jobs complete."""
    import uuid
    name = f"_kge_preflight_{uuid.uuid4().hex}"
    table = f"`{project}.{dataset}.{name}_vertex`"
    graph = f"`{project}.{dataset}.{name}`"
    failure = None
    try:
        bq.run(f"CREATE TABLE {table} (id STRING)", dataset)
        bq.run(f"CREATE PROPERTY GRAPH {graph} NODE TABLES ({table} KEY (id) LABEL Probe PROPERTIES (id))", dataset)
    except Exception as exc:
        failure = exc
    cleanup = []
    for sql in (f"DROP PROPERTY GRAPH IF EXISTS {graph}", f"DROP TABLE IF EXISTS {table}"):
        try:
            bq.run(sql, dataset)
        except Exception as exc:
            detail = f"{sql}: {exc}"
            cleanup.append(detail)
            break
    if failure or cleanup:
        detail = str(failure) if failure else "probe created, but cleanup failed"
        lower = detail.lower()
        if isinstance(failure, (TimeoutError, ConnectionError)) or any(
                word in lower for word in ("http 429", "http 50", "http 51", "ratelimit", "backenderror", "timeout")):
            action = "temporary failure: retry the test drive after checking BigQuery job status"
        elif any(word in lower for word in ("edition", "reservation", "not supported", "unsupported")):
            action = ("check graph support for this job's billing assignment and dataset location; Standard does not "
                      "support graphs; Enterprise/Enterprise Plus and on-demand support graph creation "
                      "(on-demand does not support GQL)")
        elif any(word in lower for word in ("location", "region", "not found", "http 404")):
            action = "check the configured project's dataset and its location against the job's execution location"
        elif any(word in lower for word in ("permission", "access denied", "accessdenied", "forbidden", "http 403")):
            action = ("grant the job account bigquery.propertyGraphs.create and bigquery.propertyGraphs.delete, "
                      "plus table create/read/delete permissions on this dataset")
        else:
            action = "check the BigQuery error and this dataset's graph capability before retrying"
        suffix = "; cleanup failed: " + "; ".join(cleanup) if cleanup else ""
        raise RuntimeError(f"BigQuery graph creation preflight for {project}.{dataset}: {detail}; {action}{suffix}") from failure


def preflight_loads(client: str, warehouse_loads: list[int], chunk_texts: list[str]) -> None:
    """Check staging before onboarding writes, using the selected backends' planned load sizes. Each chunk budget
    contains its text and all possible metadata values, supplied by upfront. Float JSON uses at most 24 characters
    plus separators per component; fixed row fields and the model name are added without requesting embeddings.
    This conservative estimate may require staging before the actual serialized rows cross the inline threshold."""
    import json
    from kge.adapters.bigquery import BigQueryLoader
    from kge.adapters.retrieval import backend as retrieval
    from kge.adapters.warehouse import backend as warehouse
    sizes = list(warehouse_loads) if warehouse() == "bigquery" else []
    if retrieval() == "bigquery" and chunk_texts:
        from kge.adapters.model.embed import DIM, DEFAULT_VERTEX_MODEL, HashEmbedder, LocalEmbedder, vertex_dim
        from kge.pipeline.envfile import load_env
        from kge.pipeline.settings import settings
        load_env()
        config = settings()
        provider = (config.EMBEDDING_PROVIDER or "hash").strip().lower() or "hash"
        if provider == "hash":
            dim, name = DIM, HashEmbedder.name
        elif provider == "local":
            dim, name = DIM, f"{LocalEmbedder.model_name}+jl{DIM}"
        elif provider == "vertex":
            dim = vertex_dim()
            name = (config.VERTEX_EMBEDDING_MODEL or config.EMBEDDING_MODEL or "").strip() or DEFAULT_VERTEX_MODEL
        else:
            raise ValueError(f"EMBEDDING_PROVIDER must be hash, local or vertex, not {provider!r}")
        overhead = dim * 26 + len(json.dumps(name).encode("utf-8")) + 1024
        sizes.append(sum(len(json.dumps(text, ensure_ascii=False).encode("utf-8")) + overhead for text in chunk_texts))
    if sizes:
        BigQueryLoader(client=client).preflight_staging(max(sizes))


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
