"""The demo's import layers. Each module sits in a layer, and imports only from its own layer or the ones below it, so a
change low down never needs to know what sits above it. Top to bottom:

  scripts   scripts/, examples/, kit/, kge/cli/: entry points run on their own, which nothing imports
  app       kge/app.py: the app, which mounts every service's routes, the page and /mcp
  routes    each service's HTTP alone (kge/services/<service>/routes.py, the ask service's mcp.py) and what they share
            (kge/services/http.py)
  service   each service's logic (kge/services/<service>/service.py, the ask service's models, models.py, the onboard
            service's job, jobs.py, the tools' markdown.py, the identity service's command line, cli.py)
  improve   the improvement job, which reads the estate's DDL to gather its evidence (kge/improve/gather.py)
  estate    the generated estate and onboarding
  eval      the golden sets, harness, margin and parity
  core      the engine that answers (kge/services/ask/engine/), sign-in identities (kge/services/identity/service.py)
            and the shared model (kge/semantics/): what the batch side runs too
  graph     graph load, release, history
  compiler  the pack compiler
  packstore the pack store
  pipeline  profile, state, object store, the SQL gate (kge/pipeline/sqlguard.py) and the run trace's recording side
            (kge/pipeline/trace.py); and the adapters (kge/adapters/), the model and embedding clients among them, which
            read the settings and the profile and which the pipeline's modules call
  landing   the landing zone
  ports     the protocols adapters offer (kge/ports.py)

A module's layer is its folder's (kge/<folder>/, or the top level for the scripts), but under kge/services/, where it is
its kind's: routes, service or core, as SERVICE_LAYERS says. A service package's own __init__.py is core: what it offers
the other services sits there. improve sits above estate, not below it as first drafted: kge/improve/gather.py imports
kge.estate.ddl, and nothing in estate imports improve. Every module the scan finds must sit in a layer: a new top-level
folder fails here until it is placed.

An import that goes up is allowed only where EXCEPTIONS names it, matched by importing module and imported module (never
by line, so an edit elsewhere in the file does not break it), each with its reason."""
from __future__ import annotations

import ast
from pathlib import Path

DEMO = Path(__file__).resolve().parents[2]


def test_kge_top_level_never_references_portal():
    references = [path.relative_to(DEMO) for path in (DEMO / "kge").glob("*.py")
                  if "portal" in path.read_text().lower()]
    assert references == []


SKIPPED = {".venv", "tests", "node_modules", "__pycache__"}
LAYERS = ("scripts", "app", "routes", "service", "improve", "estate", "eval", "core", "graph", "compiler", "packstore", "pipeline",
          "landing", "ports")
RANK = {name: len(LAYERS) - i for i, name in enumerate(LAYERS)}
FOLDER_LAYERS = {"cli": "scripts", "scripts": "scripts", "examples": "scripts", "kit": "scripts", "app": "app", "improve": "improve",
                 "estate": "estate", "eval": "eval", "semantics": "core", "graph": "graph", "compiler": "compiler",
                 "packstore": "packstore", "pipeline": "pipeline", "landing": "landing", "services": "core",
                 "ports": "ports", "adapters": "pipeline"}
SERVICE_LAYERS = {"routes": "routes", "mcp": "routes", "http": "routes", "service": "service", "jobs": "service", "cli": "service",
                  "markdown": "service", "models": "service", "test_drive": "service", "test_drive_run": "service", "engine": "core"}

EXCEPTIONS = {
    ("kge.services.ask.engine.context", "kge.app"): "the sample agent posts /context to the API's own app in this process, by design",
    ("kge.pipeline.objstore", "kge.graph.release"): "onboard_request reads the release state a pending onboarding is checked against",
    ("kge.graph.history", "kge.eval.sets"): "the release history names the golden sets each release was scored on",
    ("kge.pipeline.objstore", "kge.packstore"): "onboard_request reads the candidate an improvement proposed from the pack store",
    ("kge.pipeline.unit_history", "kge.packstore"): "a unit's history reads the packs the pack store holds",
    ("kge.graph.history", "kge.estate.onboard"): "restoring a version rebuilds the units onboarded since, by name at call time",
    ("kge.graph.release", "kge.estate.onboard"): "make release rebuilds the onboarded units of the release it swaps to, by name at call time",
    ("kge.services.http", "kge.app"): "the routes read the app's state as each request is served, by name, never while the app imports",
}


def module_name(path: Path, root: Path) -> str:
    parts = list(path.relative_to(root).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def folder(module: str) -> str:
    """The top-level folder a module is in, inside the demo's package: kge.semantics.query is in semantics, kge.packstore
    in packstore."""
    parts = module.split(".")
    return parts[1] if parts[0] == "kge" and len(parts) > 1 else parts[0]


def layer_of(module: str) -> str | None:
    parts = module.split(".")
    if parts[:2] == ["kge", "services"] and len(parts) > 2:
        kind = parts[2] if len(parts) == 3 else parts[3]
        if parts[2] == "identity" and kind == "service":
            return "core"
        return SERVICE_LAYERS.get(kind, "core" if len(parts) <= 3 else None)
    return FOLDER_LAYERS.get(folder(module))


def imported(tree: ast.AST, module: str, is_package: bool, known: set[str]) -> list[tuple[str, int]]:
    """Every module this one imports, at any depth (a lazy import counts as much as one at the top), as the module's
    full name: `from kge.services.ask.engine import runtrace` is kge.services.ask.engine.runtrace where that is a module, else
    kge.services.ask.engine. A module imported by name, importlib.import_module("kge.app") or import_module("kge.app"),
    counts as well."""
    package = module if is_package else module.rpartition(".")[0]
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [(a.name, node.lineno) for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                anchor = package.split(".")[:len(package.split(".")) - (node.level - 1)] if package else []
                base = ".".join([*anchor, *([base] if base else [])])
            for a in node.names:
                full = f"{base}.{a.name}" if base else a.name
                out.append((full if full in known else base, node.lineno))
        elif (isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant)
              and isinstance(node.args[0].value, str)
              and getattr(node.func, "attr", getattr(node.func, "id", None)) == "import_module"):
            out.append((node.args[0].value, node.lineno))
    return out


def shim(root: Path, top: str) -> bool:
    """A top-level folder that is a compatibility shim (its __init__.py says so), names for the frozen snapshot tests and
    no code of its own: in no layer, and never scanned."""
    init = root / top / "__init__.py"
    return init.is_file() and init.read_text().startswith('"""Compatibility shim')


def modules(root: Path) -> dict[str, Path]:
    """Every module under root by its full name, but the demo's package marker (kge/__init__.py, which holds no code) and
    the compatibility shims."""
    found = {}
    for path in sorted(root.rglob("*.py")):
        parts = path.relative_to(root).parts
        if SKIPPED.isdisjoint(parts) and not (len(parts) > 1 and shim(root, parts[0])) and module_name(path, root) != "kge":
            found[module_name(path, root)] = path
    return found


def upward_imports(root: Path, exceptions: dict = EXCEPTIONS) -> tuple[list[str], list[str]]:
    """(upward imports no exception names, modules in no layer) under root."""
    found = modules(root)
    known = set(found)
    upward, unplaced = [], []
    for module, path in found.items():
        mine = layer_of(module)
        if mine is None:
            unplaced.append(f"{path.relative_to(root)}: in no layer")
            continue
        for target, line in imported(ast.parse(path.read_text(), str(path)), module, path.name == "__init__.py", known):
            theirs = layer_of(target)
            if theirs is None or folder(target) not in FOLDER_LAYERS:
                continue
            if RANK[theirs] > RANK[mine] and (module, target) not in exceptions:
                upward.append(f"{path.relative_to(root)}:{line}: {module} ({mine}) imports {target} ({theirs})")
    return upward, unplaced


def test_imports_only_go_down_the_layers():
    upward, unplaced = upward_imports(DEMO)
    assert not unplaced, "place each in a layer (FOLDER_LAYERS):\n" + "\n".join(unplaced)
    assert not upward, "an import that goes up a layer: move the code down, or name it in EXCEPTIONS with its reason:\n" + "\n".join(upward)


def test_every_exception_is_still_needed():
    """An exception no import uses any more is removed, so the list says only what is true today."""
    used = set()
    found = modules(DEMO)
    for module, path in found.items():
        used |= {(module, target) for target, _ in imported(ast.parse(path.read_text()), module, path.name == "__init__.py", set(found))}
    assert set(EXCEPTIONS) <= used, f"no longer imported: {sorted(set(EXCEPTIONS) - used)}"


def test_a_new_upward_import_fails(tmp_path):
    """The check itself, on a synthetic tree: a pipeline module that imports the engine fails; the same import named as
    an exception passes; a lazy import inside a function counts; a folder in no layer is reported."""
    (tmp_path / "semantics").mkdir()
    (tmp_path / "semantics" / "__init__.py").write_text("")
    (tmp_path / "semantics" / "core.py").write_text("from pipeline import helper\n")
    (tmp_path / "pipeline").mkdir()
    (tmp_path / "pipeline" / "__init__.py").write_text("")
    (tmp_path / "pipeline" / "helper.py").write_text("def f():\n    from semantics import core\n    return core\n")
    upward, unplaced = upward_imports(tmp_path)
    assert upward == ["pipeline/helper.py:2: pipeline.helper (pipeline) imports semantics.core (core)"] and not unplaced
    assert upward_imports(tmp_path, {("pipeline.helper", "semantics.core"): "synthetic"}) == ([], [])
    (tmp_path / "newfolder").mkdir()
    (tmp_path / "newfolder" / "x.py").write_text("import pipeline.helper\n")
    assert upward_imports(tmp_path)[1] == ["newfolder/x.py: in no layer"]


HTTP = {"kge.app", "kge.services.http"}


def http_module(module: str) -> bool:
    """A module of the HTTP side: the app, what the routes share, each service's routes and the ask service's MCP server."""
    return module in HTTP or module.endswith((".routes", ".mcp"))


def test_a_service_reaches_another_only_through_its_package():
    """A service imports another service only by that service's package (kge.services.ask, which offers what the others
    use), never a module inside it; what every service's routes share is kge/services/http.py."""
    found = modules(DEMO)
    crossing = []
    for module, path in found.items():
        parts = module.split(".")
        if parts[:2] != ["kge", "services"] or len(parts) < 3:
            continue
        for target, line in imported(ast.parse(path.read_text()), module, path.name == "__init__.py", set(found)):
            other = target.split(".")
            if other[:2] == ["kge", "services"] and len(other) > 3 and other[2] not in (parts[2], "http"):
                crossing.append(f"{path.relative_to(DEMO)}:{line}: {module} imports {target}")
    assert crossing == [], "\n".join(crossing)


def test_only_the_http_side_imports_the_web_framework():
    """The services' logic, the engine, the shared model and the batch side import no fastapi or starlette: only the app,
    the routes and what they share do."""
    found = modules(DEMO)
    web = []
    for module, path in found.items():
        if not module.startswith("kge.") and module != "kge" or http_module(module):
            continue
        for target, line in imported(ast.parse(path.read_text()), module, path.name == "__init__.py", set(found)):
            if target.split(".")[0] in ("fastapi", "starlette"):
                web.append(f"{path.relative_to(DEMO)}:{line}: {module} imports {target}")
    assert web == [], "\n".join(web)


def backend_imports(kind: str, backends: tuple[str, ...]) -> list[str]:
    """Each import of one of a kind's backend modules (kge.adapters.<kind>.<backend>) from outside kge/adapters/<kind>/:
    a caller reaches the kind through its Protocol (kge/ports.py) and the factory in its package, never a backend."""
    found = modules(DEMO)
    inside = f"kge.adapters.{kind}"
    named = {f"{inside}.{b}" for b in backends}
    out = []
    for module, path in found.items():
        if module == inside or module.startswith(inside + "."):
            continue
        for target, line in imported(ast.parse(path.read_text()), module, path.name == "__init__.py", set(found)):
            if target in named or any(target.startswith(n + ".") for n in named):
                out.append(f"{path.relative_to(DEMO)}:{line}: {module} imports {target}")
    return out


def test_the_warehouses_are_reached_through_their_port():
    """The warehouse adapters (kge/adapters/warehouse/): no module outside them imports a backend's module, the ask service
    imports neither DuckDB nor a Google client, and each backend's class offers every member of WarehouseSet."""
    import inspect

    from kge.adapters.warehouse.bigquery import BigQueryWarehouses
    from kge.adapters.warehouse.duckdb import Warehouses
    from kge.ports import WarehouseSet

    assert backend_imports("warehouse", ("duckdb", "bigquery", "bigquery_estate", "types")) == []
    found = modules(DEMO)
    engine = []
    for module, path in found.items():
        if not module.startswith("kge.services.ask."):
            continue
        for target, line in imported(ast.parse(path.read_text()), module, path.name == "__init__.py", set(found)):
            if target.split(".")[0] in ("duckdb", "google"):
                engine.append(f"{path.relative_to(DEMO)}:{line}: {module} imports {target}")
    assert engine == [], "\n".join(engine)
    members = [m for m in dir(WarehouseSet) if not m.startswith("_")] + list(WarehouseSet.__annotations__)
    methods = [m for m in members if callable(getattr(WarehouseSet, m, None))]
    for cls in (Warehouses, BigQueryWarehouses):
        assert all(callable(getattr(cls, m, None)) for m in methods), cls
        assert list(inspect.signature(getattr(cls, "gate")).parameters) == list(inspect.signature(WarehouseSet.gate).parameters)
    offline = BigQueryWarehouses(Path("warehouses"), ["retail"], ["xref"], project="stand-in", client="stand-in")
    assert isinstance(offline, WarehouseSet) and offline.units == ["retail"] and offline.curated == ["xref"]


def test_only_the_adapters_import_duckdb_or_a_google_cloud_client():
    """DuckDB and the Google Cloud client libraries are backends: only kge/adapters/ imports them, so the estate, onboarding
    and every service reach a warehouse through its port (kge/ports.py) and its factory, and a step on the BigQuery path
    never loads DuckDB."""
    found = modules(DEMO)
    outside = []
    for module, path in found.items():
        if not module.startswith("kge.") or module.startswith("kge.adapters."):
            continue
        for target, line in imported(ast.parse(path.read_text()), module, path.name == "__init__.py", set(found)):
            if target.split(".")[0] == "duckdb" or target == "google.cloud" or target.startswith("google.cloud."):
                outside.append(f"{path.relative_to(DEMO)}:{line}: {module} imports {target}")
    assert outside == [], "\n".join(outside)


def test_the_estate_s_warehouses_are_reached_through_their_port_and_factory(tmp_path, monkeypatch):
    """The estate's warehouse port (kge/ports.py EstateWarehouses, WarehouseSession): each backend offers every member,
    and the factory picks the backend from WAREHOUSE_BACKEND, BigQuery's with no file."""
    from kge.adapters.warehouse import open_estate
    from kge.adapters.warehouse.bigquery_estate import BigQueryEstate, BigQuerySession
    from kge.adapters.warehouse.duckdb import DuckDBEstate, DuckDBSession
    from kge.ports import EstateWarehouses, WarehouseSession

    for port, classes in ((EstateWarehouses, (DuckDBEstate, BigQueryEstate)), (WarehouseSession, (DuckDBSession, BigQuerySession))):
        methods = [m for m in dir(port) if not m.startswith("_") and callable(getattr(port, m, None))]
        for cls in classes:
            assert all(callable(getattr(cls, m, None)) for m in methods), (cls, port)
    monkeypatch.delenv("WAREHOUSE_BACKEND", raising=False)
    files = open_estate(tmp_path, "meridian")
    assert isinstance(files, DuckDBEstate) and files.holds_files() and not files.kept_in("kge_meridian_warehouses")
    monkeypatch.setenv("WAREHOUSE_BACKEND", "bigquery")
    estate = open_estate(tmp_path, "meridian")
    assert isinstance(estate, BigQueryEstate) and isinstance(estate, EstateWarehouses)
    assert estate.kept_in("kge_meridian_warehouses") and not estate.holds_files() and list(tmp_path.iterdir()) == []


def test_app_state_is_reached_through_its_port_and_factory(tmp_path, monkeypatch):
    """App state (kge/adapters/appstate/): no module outside the package imports a backend's module (files, bigquery),
    only the BigQuery backend imports google, and each backend offers the AppState protocol."""
    from kge import packstore
    from kge.adapters.appstate.bigquery import BigQueryState
    from kge.adapters.appstate.files import FilesState
    from kge.pipeline.profile import Profile
    from kge.ports import AppState
    backends = {"kge.adapters.appstate.files", "kge.adapters.appstate.bigquery"}
    found = modules(DEMO)
    wrong = []
    for module, path in found.items():
        inside = module.startswith("kge.adapters.appstate")
        for target, line in imported(ast.parse(path.read_text()), module, path.name == "__init__.py", set(found)):
            if target in backends and not inside:
                wrong.append(f"{path.relative_to(DEMO)}:{line}: {module} imports {target}, a backend: use kge.adapters.appstate")
            if inside and target.split(".")[0] == "google" and module != "kge.adapters.appstate.bigquery":
                wrong.append(f"{path.relative_to(DEMO)}:{line}: {module} imports {target}")
    assert wrong == [], "\n".join(wrong)
    monkeypatch.setenv("BIGQUERY_PROJECT", "pilot-project")
    prof = Profile("meridian", "meridian", *[tmp_path] * 6, None, tmp_path / "profiles.yaml")
    for state in (FilesState(packstore.Store(tmp_path)), BigQueryState(prof)):
        assert isinstance(state, AppState), type(state).__name__


def test_the_object_store_is_reached_through_its_port_and_factory(tmp_path):
    """The object store (kge/adapters/objstore/): no module outside the package imports a backend's module (local, gcs),
    only the GCS backend imports google, and each backend offers the ObjectStore protocol."""
    from kge.adapters.objstore.gcs import GcsStore
    from kge.adapters.objstore.folder import LocalStore
    from kge.ports import ObjectStore
    backends = {"kge.adapters.objstore.folder", "kge.adapters.objstore.gcs"}
    found = modules(DEMO)
    wrong = []
    for module, path in found.items():
        inside = module.startswith("kge.adapters.objstore")
        for target, line in imported(ast.parse(path.read_text()), module, path.name == "__init__.py", set(found)):
            if target in backends and not inside:
                wrong.append(f"{path.relative_to(DEMO)}:{line}: {module} imports {target}, a backend: use kge.adapters.objstore")
            if inside and target.split(".")[0] == "google" and module != "kge.adapters.objstore.gcs":
                wrong.append(f"{path.relative_to(DEMO)}:{line}: {module} imports {target}")
    assert wrong == [], "\n".join(wrong)
    for store in (LocalStore(f"file://{tmp_path}", tmp_path), GcsStore("gs://bucket", "bucket")):
        assert isinstance(store, ObjectStore), type(store).__name__


def test_the_pack_store_and_the_pipeline_import_each_other_in_no_cycle():
    """kge/packstore.py imports no module of kge/pipeline/ (the object store it keeps its files in is the adapter's), and
    no chain of imports, lazy ones included, leads from it back to itself: the pipeline's modules that read packs
    (the object store's job runner, a unit's history) import it one way only. The check is by module: the adapters it
    imports read the pipeline's settings, which import nothing above them."""
    found = modules(DEMO)
    known = set(found)
    edges = {module: {target for target, _ in imported(ast.parse(path.read_text()), module, path.name == "__init__.py", known)
                      if target in known}
             for module, path in found.items()}
    for module in list(edges):
        parts = module.split(".")
        edges[module] |= {".".join(parts[:i]) for i in range(2, len(parts))} & known
    assert not [t for t in edges["kge.packstore"] if t.startswith("kge.pipeline.")], sorted(edges["kge.packstore"])
    seen, stack, back = set(), list(edges["kge.packstore"]), []
    while stack:
        module = stack.pop()
        if module == "kge.packstore":
            back.append(module)
            break
        if module in seen:
            continue
        seen.add(module)
        stack += edges.get(module, ())
    assert back == [], "kge.packstore reaches itself through: " + ", ".join(sorted(m for m in seen if m.startswith("kge.pipeline")))


MODEL_LIBRARIES = {"anthropic", "langchain_core", "sentence_transformers"}


def test_only_the_model_adapter_imports_the_model_libraries_and_each_backend_is_a_port(monkeypatch):
    """The model SDKs (anthropic, langchain_core's chat model, sentence-transformers) are imported only under
    kge/adapters/model/; the model client, the mock and every embedder offer the ports' ModelProvider and Embeddings."""
    import sys
    import types

    from kge.adapters.model import embed, providers
    from kge.ports import Embeddings, ModelProvider

    found = modules(DEMO)
    outside = []
    for module, path in found.items():
        if module.startswith("kge.adapters.model"):
            continue
        for target, line in imported(ast.parse(path.read_text()), module, path.name == "__init__.py", set(found)):
            if target.split(".")[0] in MODEL_LIBRARIES:
                outside.append(f"{path.relative_to(DEMO)}:{line}: {module} imports {target}")
    assert outside == [], "\n".join(outside)

    for llm in (providers.LLM(providers.MOCK, providers.MOCK), providers.LLM("openai", "a-model")):
        assert isinstance(llm, ModelProvider), llm.name

    class Model:
        def __init__(self, name):
            pass

        def get_sentence_embedding_dimension(self):
            return embed.DIM

        def encode(self, text, normalize_embeddings=True):
            return [1.0] * embed.DIM

    monkeypatch.setitem(sys.modules, "sentence_transformers", types.SimpleNamespace(SentenceTransformer=Model))
    for embedder in (embed.HashEmbedder(), embed.LocalEmbedder(), embed.VertexEmbedder(project="a-project")):
        assert isinstance(embedder, Embeddings), type(embedder).__name__


def test_retrieval_is_reached_through_its_port(monkeypatch):
    """The retrieval adapters (kge/adapters/retrieval/): no module outside them imports a backend's module, each backend
    offers Retrieval, and open_retrieval picks BigQuery where RETRIEVAL_BACKEND, or else WAREHOUSE_BACKEND, names it."""
    from kge.adapters.retrieval import open_retrieval
    from kge.adapters.retrieval.bigquery import BigQueryRetrieval
    from kge.adapters.retrieval.graph import GraphRetrieval
    from kge.ports import Retrieval

    assert backend_imports("retrieval", ("graph", "bigquery")) == []
    assert isinstance(GraphRetrieval(None), Retrieval) and isinstance(BigQueryRetrieval(None), Retrieval)
    for retrieval, warehouse, chosen in (("", "", GraphRetrieval), ("", "bigquery", BigQueryRetrieval),
                                         ("ladybug", "bigquery", GraphRetrieval), ("bigquery", "duckdb", BigQueryRetrieval)):
        monkeypatch.setenv("RETRIEVAL_BACKEND", retrieval)
        monkeypatch.setenv("WAREHOUSE_BACKEND", warehouse)
        assert type(open_retrieval(None)) is chosen, (retrieval, warehouse)


GRAPH_LIBRARIES = {"ladybug", "real_ladybug", "kuzu"}


def test_only_the_graph_store_adapter_imports_the_graph_database_and_it_is_a_port():
    """The graph database's library (ladybug, or kuzu by its old name) is imported only under kge/adapters/graphstore/,
    by an import statement or by its name through importlib; the store the package gives is the ports' GraphStore."""
    import re

    from kge.adapters import graphstore
    from kge.adapters.graphstore.ladybug import LadybugStore
    from kge.ports import GraphStore

    by_name = re.compile(r"import_module\(\s*['\"](?:real_)?(?:ladybug|kuzu)['\"]")
    found = modules(DEMO)
    outside = []
    for module, path in found.items():
        if module.startswith("kge.adapters.graphstore"):
            continue
        text = path.read_text()
        for target, line in imported(ast.parse(text), module, path.name == "__init__.py", set(found)):
            if target.split(".")[0] in GRAPH_LIBRARIES:
                outside.append(f"{path.relative_to(DEMO)}:{line}: {module} imports {target}")
        if by_name.search(text):
            outside.append(f"{path.relative_to(DEMO)}: {module} imports the graph database by name")
    assert outside == [], "\n".join(outside)
    assert isinstance(LadybugStore(), GraphStore) and isinstance(graphstore.store(), GraphStore)
    assert graphstore.STORE == "ladybug"


def test_no_module_outside_the_graph_store_adapter_reaches_a_store_backend():
    """A graph store's backend (kge/adapters/graphstore/ladybug.py, bigquery.py) is the factory's to import: the engine,
    the graph's load, release and history, the pipeline and every other caller take the store from the package
    (store(), connect, open_db, ...), by name or through importlib alike. The dialect modules beside them (cypher.py,
    the Cypher subset; gql.py, its BigQuery form and the BigQuery DDL the compiler emits) and base.py are not backends."""
    backends = {f"kge.adapters.graphstore.{name}" for name in ("ladybug", "bigquery")}
    found = modules(DEMO)
    reached = []
    for module, path in found.items():
        if module.startswith("kge.adapters.graphstore"):
            continue
        for target, line in imported(ast.parse(path.read_text()), module, path.name == "__init__.py", set(found)):
            if target in backends or any(target.startswith(b + ".") for b in backends):
                reached.append(f"{path.relative_to(DEMO)}:{line}: {module} imports {target}")
    assert reached == [], "\n".join(reached)


def test_a_module_imported_by_name_counts_as_an_import(tmp_path):
    """importlib.import_module("...") and import_module("...") with a literal name are imports like any other: an upward
    one fails unless EXCEPTIONS names it."""
    for name in ("semantics", "pipeline"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "__init__.py").write_text("")
    (tmp_path / "semantics" / "core.py").write_text("")
    (tmp_path / "pipeline" / "a.py").write_text('import importlib\n\ndef f():\n    return importlib.import_module("semantics.core")\n')
    (tmp_path / "pipeline" / "b.py").write_text('from importlib import import_module\nx = import_module("semantics.core")\n')
    upward, unplaced = upward_imports(tmp_path)
    assert upward == ["pipeline/a.py:4: pipeline.a (pipeline) imports semantics.core (core)",
                      "pipeline/b.py:2: pipeline.b (pipeline) imports semantics.core (core)"] and not unplaced


BACKEND_SETTINGS = {"WAREHOUSE_BACKEND", "RETRIEVAL_BACKEND", "APP_STATE_BACKEND", "GRAPH_STORE", "GRAPH_DATASET"}
BACKEND_NAMES = {"bigquery", "duckdb", "ladybug", "kuzu"}
BIGQUERY_TYPE_NAMES = {"BOOL", "INT64", "FLOAT64", "STRING"}
GRAPH_MODEL_TYPES = {"kge/compiler/emit.py": "the graph model's own column types (STRING, INT64, DOUBLE, BOOLEAN, the meta-model's "
                                             "and its DDL's), which share two names with BigQuery's and are no warehouse's"}
CHOOSERS = {"graph_kind", "backend", "store_name"}
DIALECTS = {"sql_dialect", "query_dialect", "dialect", "AUTHORED"}
SEARCHES = ("find", "rfind", "index", "rindex", "count", "__contains__")
PATTERNS = ("search", "match", "fullmatch", "findall", "finditer", "sub", "split", "compile")
BACKEND_PREDICATES = {"gcp_native"}
STRING_METHODS = {"lower", "upper", "casefold", "strip", "lstrip", "rstrip", "replace", "title", "capitalize"}


def _named(node) -> str:
    return node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute) else ""


def _backend_name(node) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.strip().lower() in BACKEND_NAMES


def _bigquery_type_name(node) -> bool:
    """BigQuery's own name for a column type, which every catalogue here names DuckDB's way (BOOLEAN, BIGINT, DOUBLE,
    VARCHAR) once its warehouse adapter has read it (kge/adapters/warehouse/types.py catalogue_type)."""
    return isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.strip().upper() in BIGQUERY_TYPE_NAMES


def _store_name(node) -> bool:
    """STORE, or a store's name (store().name)."""
    return _named(node) == "STORE" or (isinstance(node, ast.Attribute) and node.attr == "name" and isinstance(node.value, ast.Call)
                                       and _named(node.value.func) == "store")


def _backend_fragment(node) -> bool:
    """A string that names a backend in part: it holds a backend's name, or begins one ("big", "Duck"); a file's suffix
    (".duckdb") names a file's kind, not a backend, and is not one."""
    if not (isinstance(node, ast.Constant) and isinstance(node.value, str)) or node.value.startswith("."):
        return False
    text = node.value.strip().lower()
    return len(text) >= 3 and any(b in text or b.startswith(text) for b in BACKEND_NAMES)


def _names_backend(node) -> bool:
    """A string that holds a backend's name, in any case ("BigQueryWarehouses", "DuckDB"); a file's suffix (".duckdb")
    names a file's kind, not a backend, and is not one."""
    return (isinstance(node, ast.Constant) and isinstance(node.value, str) and not node.value.startswith(".")
            and any(b in node.value.lower() for b in BACKEND_NAMES))


def _type_name(node) -> bool:
    """type(x).__name__ or x.__class__.__name__: a value's class name."""
    if not (isinstance(node, ast.Attribute) and node.attr == "__name__"):
        return False
    inner = node.value
    return (isinstance(inner, ast.Call) and _named(inner.func) == "type") or (isinstance(inner, ast.Attribute)
                                                                              and inner.attr == "__class__")


def _folded(node) -> str | None:
    """The string an expression of constants makes ("big" + "query", f"{'Duck'}DB", "Big Query".replace(" ", "")),
    or None where it takes any value that is not a constant."""
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _folded(node.left), _folded(node.right)
        return left + right if left is not None and right is not None else None
    if isinstance(node, ast.JoinedStr):
        parts = [_folded(v.value if isinstance(v, ast.FormattedValue) else v) for v in node.values]
        return "".join(parts) if all(p is not None for p in parts) else None
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in STRING_METHODS:
        base, args = _folded(node.func.value), [_folded(a) for a in node.args]
        if base is None or any(a is None for a in args) or node.keywords:
            return None
        try:
            return getattr(base, node.func.attr)(*args)
        except (TypeError, ValueError):
            return None
    return None


def _built_name(node) -> str | None:
    """A backend setting's name, or a string naming a backend, that an expression builds from constants rather than
    writes out: what a concatenation, an f-string or a string method makes, a file's suffix aside."""
    if isinstance(node, ast.Constant):
        return None
    text = _folded(node)
    if text is None or text.startswith("."):
        return None
    if text in BACKEND_SETTINGS or any(b in text.lower().replace(" ", "") for b in BACKEND_NAMES):
        return text
    return None


def _type_module(node) -> bool:
    """type(x).__module__ or x.__class__.__module__: the module a value's class is defined in."""
    if not (isinstance(node, ast.Attribute) and node.attr == "__module__"):
        return False
    inner = node.value
    return (isinstance(inner, ast.Call) and _named(inner.func) == "type") or (isinstance(inner, ast.Attribute)
                                                                              and inner.attr == "__class__")


SETTING_FRAGMENTS = ("BACKEND", "GRAPH_STORE", "GRAPH_DATASET")


def _setting_built(node) -> bool:
    """A concatenation or an f-string with a variable part whose constant pieces name a backend setting in part
    (f"{kind}_BACKEND", kind.upper() + "_BACKEND")."""
    if isinstance(node, ast.JoinedStr):
        pieces = [v for v in node.values if isinstance(v, ast.Constant)]
    elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        pieces = [n for n in (node.left, node.right) if isinstance(n, ast.Constant)]
    else:
        return False
    return any(isinstance(c.value, str) and any(f in c.value for f in SETTING_FRAGMENTS) for c in pieces)


def adapter_classes() -> set[str]:
    """Every class kge/adapters/ defines but its exceptions: a backend's own type (LocalStore, FilesState, Warehouses,
    HashEmbedder ...), which a caller never tests a value against."""
    defined: dict[str, list[str]] = {}
    for path in sorted((DEMO / "kge" / "adapters").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ClassDef):
                defined.setdefault(node.name, []).extend(_named(b) for b in node.bases)
    errors = {"Exception", "BaseException", "RuntimeError", "ValueError", "OSError", "LookupError", "KeyError",
              "TypeError", "PermissionError", "FileNotFoundError", "StoreError", "StoreConflict", "WarehouseRefused"}
    grown = True
    while grown:
        more = {n for n, bases in defined.items() if n not in errors and any(b in errors or b.endswith(("Error", "Exception"))
                                                                               for b in bases)}
        grown = bool(more)
        errors |= more
    return {n for n in defined if n not in errors and not n.endswith(("Error", "Exception"))}


ADAPTER_CLASSES = adapter_classes()


def _backend_classes(tree) -> set[str]:
    """The names a module binds to a class an adapter defines (ADAPTER_CLASSES, imported from kge.adapters or any module
    under it, the factory's re-exports among them), or to anything imported from a backend's module
    (kge.adapters.warehouse.bigquery's BigQueryWarehouses, say), as "Name"; and each such class reached through a module
    of kge.adapters a name is bound to, as "module.Class"."""
    out, modules = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("kge.adapters"):
            backend = any(b in part for part in node.module.split(".") for b in BACKEND_NAMES)
            for a in node.names:
                if a.name in ADAPTER_CLASSES or backend:
                    out.add(a.asname or a.name)
                else:
                    modules.add(a.asname or a.name)
        elif isinstance(node, ast.Import):
            modules |= {a.asname for a in node.names if a.name.startswith("kge.adapters") and a.asname}
    return out | {f"{m}.{c}" for m in modules for c in ADAPTER_CLASSES}


def _class_ref(node) -> str:
    """How node names a class: "Name", or "module.Class" for an attribute of a name."""
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        return f"{node.value.id}.{node.attr}"
    return _named(node)


def _backend_constants(tree) -> set[str]:
    """The module-level names bound to a string that names a backend (B = "BigQueryWarehouses")."""
    out = set()
    for node in getattr(tree, "body", []):
        if isinstance(node, ast.Assign) and (_names_backend(node.value) or _built_name(node.value)):
            out |= {t.id for t in node.targets if isinstance(t, ast.Name)}
    return out


def _affix_args(call) -> list:
    out = []
    for a in call.args:
        out += a.elts if isinstance(a, (ast.Tuple, ast.List, ast.Set)) else [a]
    return out


def _tests(tree) -> list:
    """Every expression a module decides on: an if's, a while's, a ternary's, an assert's and a comprehension's tests."""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.If, ast.While, ast.IfExp, ast.Assert)):
            out.append(node.test)
        elif isinstance(node, ast.comprehension):
            out += node.ifs
    return out


def backend_branches(package: Path) -> list[str]:
    """Where a module of package outside kge/adapters/ (and the settings module, which reads every setting) decides by
    backend: it reads a backend setting (settings().<NAME>, or its name in os.getenv or os.environ, any use of the name);
    compares a value with a backend's name ("bigquery", "duckdb", "ladybug", "kuzu" as an operand of ==, !=, in or not
    in), or with the graph store's name (STORE, store().name), or compares a dialect (sql_dialect, query_dialect,
    dialect, AUTHORED); tests the store's name, a type's name (type(x).__name__, x.__class__.__name__) or any name against
    a backend's (startswith or endswith on the first two, or with an argument that holds or begins a backend's name, a file's
    suffix such as ".duckdb" aside); compares with, or searches (find, rfind, index, rindex) for, a longer string that
    holds a backend's name in any case ("BigQueryWarehouses"), the same suffix aside;
    compares with BigQuery's name for a column type ("BOOL", "INT64", "FLOAT64", "STRING"), which the warehouse adapter
    names DuckDB's way before any caller reads it;
    sniffs a capability named for a backend (getattr or hasattr of an attribute whose name holds one); matches a case on a backend's name; keys a dict by one; compares what a backend chooser returns
    (graph_kind(), backend(), store_name()); or decides on a predicate named for a backend (a call whose name holds one,
    in an if, a while, a ternary, an assert or a comprehension's test). That decision is the factory's: the caller asks
    the port, which offers an operation that does the right thing per adapter, or names a capability it owns."""
    out = []
    for path in sorted(Path(package).rglob("*.py")):
        rel = path.relative_to(Path(package).parent).as_posix()
        if rel.startswith("kge/adapters/") or rel == "kge/pipeline/settings.py":
            continue
        tree = ast.parse(path.read_text())
        classes, constants = _backend_classes(tree), _backend_constants(tree)
        for node in ast.walk(tree):
            built = _built_name(node)
            if built is not None:
                out.append(f"{rel}:{node.lineno}: builds {built!r}")
            if isinstance(node, ast.Call) and _named(node.func) in BACKEND_PREDICATES:
                out.append(f"{rel}:{node.lineno}: calls {_named(node.func)}()")
            if _setting_built(node):
                out.append(f"{rel}:{node.lineno}: builds a backend setting's name")
            if isinstance(node, ast.Call) and _named(node.func) in ("isinstance", "issubclass") and len(node.args) == 2:
                kinds = node.args[1].elts if isinstance(node.args[1], (ast.Tuple, ast.List)) else [node.args[1]]
                for k in kinds:
                    if _class_ref(k) in classes or any(b in _named(k).lower() for b in BACKEND_NAMES):
                        out.append(f"{rel}:{node.lineno}: tests {_named(node.func)} against {_named(k)}")
            if isinstance(node, ast.Compare) and any(isinstance(op, (ast.Is, ast.IsNot, ast.Eq, ast.NotEq)) for op in node.ops):
                for o in (node.left, *node.comparators):
                    if isinstance(o, (ast.Name, ast.Attribute)) and _class_ref(o) in classes:
                        out.append(f"{rel}:{node.lineno}: compares a type with {_named(o)}")
            if _type_module(node):
                out.append(f"{rel}:{node.lineno}: reads a type's module")
            if isinstance(node, ast.Subscript) and _type_name(node.slice):
                out.append(f"{rel}:{node.lineno}: looks a type's name up")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get" \
                    and node.args and _type_name(node.args[0]):
                out.append(f"{rel}:{node.lineno}: looks a type's name up")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in PATTERNS \
                    and _named(node.func.value) == "re" and node.args and _names_backend(node.args[0]):
                out.append(f"{rel}:{node.lineno}: matches a pattern naming a backend, {node.args[0].value!r}")
            if isinstance(node, ast.Attribute) and node.attr not in BACKEND_SETTINGS and isinstance(node.ctx, ast.Load) \
                    and any(b in node.attr.lower() for b in BACKEND_NAMES):
                out.append(f"{rel}:{node.lineno}: reads .{node.attr}")
            if isinstance(node, ast.Attribute) and node.attr in BACKEND_SETTINGS:
                out.append(f"{rel}:{node.lineno}: reads {node.attr}")
            elif isinstance(node, ast.Constant) and node.value in BACKEND_SETTINGS:
                out.append(f"{rel}:{node.lineno}: names {node.value}")
            elif isinstance(node, ast.Compare):
                operands = [node.left, *node.comparators]
                operands += [x for o in operands if isinstance(o, (ast.Tuple, ast.List, ast.Set)) for x in o.elts]
                for o in operands:
                    if _bigquery_type_name(o) and rel not in GRAPH_MODEL_TYPES:
                        out.append(f"{rel}:{node.lineno}: compares with BigQuery's type name {o.value!r}")
                    if isinstance(o, ast.Name) and o.id in constants:
                        out.append(f"{rel}:{node.lineno}: compares with {o.id}, a constant naming a backend")
                    if _backend_name(o):
                        out.append(f"{rel}:{node.lineno}: compares with {o.value!r}")
                    elif _names_backend(o):
                        out.append(f"{rel}:{node.lineno}: compares with a string naming a backend, {o.value!r}")
                    elif _store_name(o):
                        out.append(f"{rel}:{node.lineno}: compares the graph store's name")
                    elif _named(o) in DIALECTS:
                        out.append(f"{rel}:{node.lineno}: compares a dialect ({_named(o)})")
                    elif isinstance(o, ast.Call) and _named(o.func) in CHOOSERS:
                        out.append(f"{rel}:{node.lineno}: compares {_named(o.func)}()")
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("startswith", "endswith"):
                if _store_name(node.func.value):
                    out.append(f"{rel}:{node.lineno}: tests the graph store's name with {node.func.attr}")
                elif _type_name(node.func.value):
                    out.append(f"{rel}:{node.lineno}: tests a type's name with {node.func.attr}")
                elif any(_backend_fragment(a) for a in _affix_args(node)):
                    out.append(f"{rel}:{node.lineno}: tests a name with {node.func.attr} for a backend's")
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in SEARCHES:
                if any(_names_backend(a) for a in _affix_args(node)):
                    out.append(f"{rel}:{node.lineno}: tests a name with {node.func.attr} for a backend's")
            elif isinstance(node, ast.Call) and _named(node.func) in ("getattr", "hasattr") and len(node.args) >= 2 \
                    and _backend_fragment(node.args[1]) and any(b in node.args[1].value.lower() for b in BACKEND_NAMES):
                out.append(f"{rel}:{node.lineno}: sniffs {node.args[1].value!r} with {_named(node.func)}")
            elif isinstance(node, ast.match_case):
                for value in ast.walk(node.pattern):
                    if isinstance(value, ast.MatchValue) and _backend_name(value.value):
                        out.append(f"{rel}:{value.lineno}: matches on {value.value.value!r}")
            elif isinstance(node, ast.Dict):
                for key in node.keys:
                    if key is not None and _backend_name(key):
                        out.append(f"{rel}:{key.lineno}: keys a dict by {key.value!r}")
        for test in _tests(tree):
            for call in (n for n in ast.walk(test) if isinstance(n, ast.Call)):
                if any(b in _named(call.func).lower() for b in BACKEND_NAMES):
                    out.append(f"{rel}:{call.lineno}: decides on {_named(call.func)}()")
    return out


def test_no_caller_decides_by_backend():
    """demo-layout's ports and adapters rule: outside kge/adapters/ no module reads a backend setting or compares with a
    backend's name; the factory in each adapter package chooses, and a caller that needs a new operation grows the port."""
    found = backend_branches(DEMO / "kge")
    assert found == [], "\n".join(found)


def test_a_planted_branch_on_a_backend_is_caught(tmp_path):
    (tmp_path / "kge" / "estate").mkdir(parents=True)
    (tmp_path / "kge" / "adapters").mkdir()
    (tmp_path / "kge" / "estate" / "x.py").write_text(
        "import os\nfrom kge.pipeline.settings import settings\n\n\ndef f():\n"
        "    if settings().WAREHOUSE_BACKEND == 'bigquery':\n        return 1\n"
        "    if os.getenv('GRAPH_STORE', '') in ('ladybug', 'kuzu'):\n        return 2\n"
        "    return 'duckdb' != os.environ.get('X')\n")
    (tmp_path / "kge" / "adapters" / "y.py").write_text("from kge.pipeline.settings import settings\nok = settings().GRAPH_STORE == 'bigquery'\n")
    assert sorted(backend_branches(tmp_path / "kge")) == sorted([
        "kge/estate/x.py:6: compares with 'bigquery'", "kge/estate/x.py:6: reads WAREHOUSE_BACKEND",
        "kge/estate/x.py:8: compares with 'ladybug'", "kge/estate/x.py:8: compares with 'kuzu'",
        "kge/estate/x.py:8: names GRAPH_STORE", "kge/estate/x.py:10: compares with 'duckdb'"])


def test_the_other_shapes_of_a_backend_branch_are_caught(tmp_path):
    """Grok m3 on #97: match and case, a dict keyed by a backend's name, the store's name, a dialect, a chooser's value,
    and a predicate named for a backend, each outside kge/adapters/; passing a dialect along is not a branch."""
    (tmp_path / "kge" / "estate").mkdir(parents=True)
    (tmp_path / "kge" / "estate" / "x.py").write_text(
        "from kge.adapters import graphstore, syncs_to_bigquery\n"
        "from kge.adapters.bigquery import graph_kind\n"
        "from kge.ports import AUTHORED\n\n\n"
        "def f(w, kind):\n"
        "    match kind:\n"
        "        case 'bigquery':\n"
        "            return 1\n"
        "    how = {'ladybug': 1, 'other': 2}\n"
        "    if graphstore.STORE == 'x' or graphstore.store().name != kind:\n"
        "        return how\n"
        "    if w.sql_dialect == AUTHORED:\n"
        "        return 3\n"
        "    if graph_kind() == 'candidates':\n"
        "        return 4\n"
        "    w.rows('u', 'SELECT 1', w.sql_dialect)\n"
        "    return 5 if syncs_to_bigquery() else 6\n")
    assert sorted(backend_branches(tmp_path / "kge")) == sorted([
        "kge/estate/x.py:10: keys a dict by 'ladybug'", "kge/estate/x.py:11: compares the graph store's name",
        "kge/estate/x.py:11: compares the graph store's name", "kge/estate/x.py:13: compares a dialect (sql_dialect)",
        "kge/estate/x.py:13: compares a dialect (AUTHORED)", "kge/estate/x.py:15: compares graph_kind()",
        "kge/estate/x.py:8: matches on 'bigquery'", "kge/estate/x.py:18: decides on syncs_to_bigquery()"])


def test_affix_tests_and_capability_sniffing_are_caught(tmp_path):
    """Grok m1 on #97: startswith or endswith on a type's name, on the store's name, or with a backend's name or prefix
    as the argument, and getattr or hasattr of an attribute named for a backend, each outside kge/adapters/."""
    (tmp_path / "kge" / "estate").mkdir(parents=True)
    (tmp_path / "kge" / "adapters").mkdir()
    (tmp_path / "kge" / "estate" / "x.py").write_text(
        "from kge.adapters import graphstore\n\n\n"
        "def f(w, name):\n"
        "    a = type(w).__name__.startswith('Warehouse')\n"
        "    b = w.__class__.__name__.endswith('Set')\n"
        "    c = graphstore.STORE.startswith('x')\n"
        "    d = graphstore.store().name.endswith('y')\n"
        "    e = name.startswith('big')\n"
        "    g = name.endswith(('Kuzu', 'other'))\n"
        "    h = getattr(w, 'is_bigquery', False)\n"
        "    i = hasattr(w, 'duckdb_conn')\n"
        "    j = name.startswith('SELECT') or getattr(w, 'rows') or hasattr(w, 'big')\n"
        "    return a, b, c, d, e, g, h, i, j\n")
    (tmp_path / "kge" / "adapters" / "y.py").write_text("def f(w):\n    return type(w).__name__.startswith('BigQuery')\n")
    assert sorted(backend_branches(tmp_path / "kge")) == sorted([
        "kge/estate/x.py:5: tests a type's name with startswith", "kge/estate/x.py:6: tests a type's name with endswith",
        "kge/estate/x.py:7: tests the graph store's name with startswith",
        "kge/estate/x.py:8: tests the graph store's name with endswith",
        "kge/estate/x.py:9: tests a name with startswith for a backend's",
        "kge/estate/x.py:10: tests a name with endswith for a backend's",
        "kge/estate/x.py:11: sniffs 'is_bigquery' with getattr", "kge/estate/x.py:12: sniffs 'duckdb_conn' with hasattr"])


def test_a_longer_backend_named_string_in_a_comparison_or_a_search_is_caught(tmp_path):
    """Grok n1 on #97: == against a string that holds a backend's name in any case (a class's name, "BigQueryWarehouses"),
    in or not in with one, and find, rfind, index or rindex with one, each outside kge/adapters/; a file's suffix
    (".duckdb") names a file's kind and stays allowed."""
    (tmp_path / "kge" / "estate").mkdir(parents=True)
    (tmp_path / "kge" / "estate" / "x.py").write_text(
        "def f(w, name):\n"
        "    a = w.__class__.__name__ == 'BigQueryWarehouses'\n"
        "    b = str(type(w)).find('BigQuery') >= 0\n"
        "    c = 'DuckDB' in str(type(w))\n"
        "    d = name.index('LadybugStore')\n"
        "    e = name.rfind('kuzu')\n"
        "    g = 'Warehouses' not in repr(w) and name != 'BIGQUERY_ESTATE'\n"
        "    h = name.endswith('.duckdb') or name == 'warehouse'\n"
        "    return a, b, c, d, e, g, h\n")
    assert sorted(backend_branches(tmp_path / "kge")) == sorted([
        "kge/estate/x.py:2: compares with a string naming a backend, 'BigQueryWarehouses'",
        "kge/estate/x.py:3: tests a name with find for a backend's",
        "kge/estate/x.py:4: compares with 'DuckDB'",
        "kge/estate/x.py:5: tests a name with index for a backend's",
        "kge/estate/x.py:6: tests a name with rfind for a backend's",
        "kge/estate/x.py:7: compares with a string naming a backend, 'BIGQUERY_ESTATE'"])


def test_a_comparison_with_a_bigquery_type_name_is_caught(tmp_path):
    """A review of #97: the engine compared a column's type with "BOOL", BigQuery's name, where every catalogue names
    types DuckDB's way. Outside kge/adapters/ a comparison with BOOL, INT64, FLOAT64 or STRING is a branch on the
    backend; the adapter's own mapping is not."""
    (tmp_path / "kge" / "services" / "ask" / "engine").mkdir(parents=True)
    (tmp_path / "kge" / "adapters").mkdir()
    (tmp_path / "kge" / "services" / "ask" / "engine" / "x.py").write_text(
        "def f(dtype):\n"
        "    a = dtype.upper() in ('BOOLEAN', 'BOOL')\n"
        "    b = dtype == 'int64' or dtype != 'FLOAT64'\n"
        "    c = dtype in {'STRING'}\n"
        "    d = dtype == 'BOOLEAN'\n"
        "    return a, b, c, d\n")
    (tmp_path / "kge" / "adapters" / "y.py").write_text("def g(t):\n    return t == 'BOOL'\n")
    assert sorted(backend_branches(tmp_path / "kge")) == sorted([
        "kge/services/ask/engine/x.py:2: compares with BigQuery's type name 'BOOL'",
        "kge/services/ask/engine/x.py:3: compares with BigQuery's type name 'int64'",
        "kge/services/ask/engine/x.py:3: compares with BigQuery's type name 'FLOAT64'",
        "kge/services/ask/engine/x.py:4: compares with BigQuery's type name 'STRING'"])


def test_each_module_holding_graph_model_types_still_compares_them():
    """GRAPH_MODEL_TYPES names a module only while it compares the graph model's own type names."""
    for rel in GRAPH_MODEL_TYPES:
        tree = ast.parse((DEMO / rel).read_text())
        assert any(isinstance(n, ast.Compare) and any(_bigquery_type_name(o) for o in (n.left, *n.comparators))
                   for n in ast.walk(tree)), rel


def test_the_verifier_s_planted_evasions_are_each_caught():
    """Verifier V1 on #97 (tests/fixtures/backend_branch/kge/estate/evade.py, its planted file as it gave it): a call to
    gcp_native(), a backend-named attribute (.bigquery_dataset), isinstance against an adapter's class, getattr of a
    setting's name built by concatenation, a comparison with a backend's name built by a string method, and a lookup
    keyed on a type's module, each outside kge/adapters/; a file's suffix (p.suffix == ".ladybug") stays allowed."""
    found = backend_branches(DEMO / "tests" / "fixtures" / "backend_branch" / "kge")
    lines = {int(f.split(":")[1]) for f in found}
    assert lines == {8, 10, 12, 14, 18, 20}, "\n".join(sorted(found))
    assert any("calls gcp_native()" in f for f in found) and any("reads .bigquery_dataset" in f for f in found)
    assert any("isinstance against BigQueryWarehouses" in f for f in found)
    assert any("builds 'WAREHOUSE_BACKEND'" in f for f in found) and any("reads a type's module" in f for f in found)


def test_counting_containing_patterns_and_constants_naming_a_backend_are_caught(tmp_path):
    """Grok n2 on #97: count and __contains__ with a backend's name, a regular expression that names one, and a
    module-level constant naming one used in a comparison, each outside kge/adapters/."""
    (tmp_path / "kge" / "estate").mkdir(parents=True)
    (tmp_path / "kge" / "estate" / "x.py").write_text(
        "import re\n\nB = 'BigQueryWarehouses'\n\n\n"
        "def f(n):\n"
        "    a = n.count('BigQuery')\n"
        "    b = re.search('duckdb', n)\n"
        "    c = n == B\n"
        "    d = n.__contains__('Ladybug')\n"
        "    e = re.match(r'select', n) or n.count('x')\n"
        "    return a, b, c, d, e\n")
    assert sorted(backend_branches(tmp_path / "kge")) == sorted([
        "kge/estate/x.py:7: tests a name with count for a backend's",
        "kge/estate/x.py:8: matches a pattern naming a backend, 'duckdb'",
        "kge/estate/x.py:9: compares with B, a constant naming a backend",
        "kge/estate/x.py:10: tests a name with __contains__ for a backend's"])


def test_an_adapter_s_class_and_a_setting_s_name_with_a_variable_part_are_caught(tmp_path):
    """Verifier V1 on #97, beyond its planted file: isinstance, issubclass, `is` or == against any class an adapter
    defines, whatever its name (LocalStore, FilesState, Warehouses, HashEmbedder), imported by name or reached through
    a module of kge.adapters; and a setting's name built with a variable part (f"{kind}_BACKEND", x + "_BACKEND"). A
    class of the same name from elsewhere (sqlglot's Column) stays allowed."""
    (tmp_path / "kge" / "estate").mkdir(parents=True)
    (tmp_path / "kge" / "estate" / "x.py").write_text(
        "import os\n"
        "from sqlglot.expressions import Column\n"
        "from kge.adapters import appstate, objstore\n"
        "from kge.adapters.objstore import LocalStore\n"
        "from kge.adapters.warehouse import Warehouses as W\n"
        "from kge.adapters.model import HashEmbedder\n"
        "from kge.pipeline.settings import settings\n\n\n"
        "def f(s, w, e, kind, c):\n"
        "    a = isinstance(s, LocalStore)\n"
        "    b = type(w) is W\n"
        "    d = issubclass(type(e), HashEmbedder)\n"
        "    g = isinstance(s, appstate.FilesState) or e.__class__ == objstore.GcsStore\n"
        "    h = getattr(settings(), f'{kind}_BACKEND')\n"
        "    i = os.getenv(kind.upper() + '_BACKEND')\n"
        "    j = isinstance(c, Column)\n"
        "    return a, b, d, g, h, i, j\n")
    assert sorted(backend_branches(tmp_path / "kge")) == sorted([
        "kge/estate/x.py:11: tests isinstance against LocalStore",
        "kge/estate/x.py:12: compares a type with W",
        "kge/estate/x.py:13: tests issubclass against HashEmbedder",
        "kge/estate/x.py:14: tests isinstance against FilesState",
        "kge/estate/x.py:14: compares a type with GcsStore",
        "kge/estate/x.py:15: builds a backend setting's name",
        "kge/estate/x.py:16: builds a backend setting's name"])


SCHEMES = ("gs:", "s3:", "file:", "gcs:")


def _scheme_literal(node: ast.AST) -> bool:
    return (isinstance(node, ast.Constant) and isinstance(node.value, str)
            and (node.value.lower().startswith(SCHEMES) or node.value.lower().rstrip(":/") in {s.rstrip(":") for s in SCHEMES}))


def scheme_branches(package: Path) -> list[str]:
    """Where a module outside kge/adapters/ decides by a store URL's scheme: compares a value with, or tests a string's
    start or end against, a literal naming a store's scheme ("gs://", "gs", "s3:", "file://"), as a parsed URL's .scheme
    compared with "gs" does; a request's http or https scheme is no store's. Which store a URL names is the object-store factory's question (kge/adapters/objstore), never a caller's."""
    out = []
    for path in sorted(package.rglob("*.py")):
        rel = path.relative_to(package.parent).as_posix()
        if rel.startswith("kge/adapters/") or rel == "kge/pipeline/settings.py":
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Compare):
                operands = [node.left, *node.comparators]
                if any(_scheme_literal(o) for o in operands):
                    out.append(f"{rel}:{node.lineno}")
            elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                  and node.func.attr in ("startswith", "endswith") and node.args):
                arg = node.args[0]
                values = arg.elts if isinstance(arg, ast.Tuple) else [arg]
                if any(_scheme_literal(v) for v in values):
                    out.append(f"{rel}:{node.lineno}")
    return out


def test_no_caller_decides_by_a_store_urls_scheme():
    assert scheme_branches(DEMO / "kge") == []


def test_a_planted_branch_on_a_url_scheme_is_caught(tmp_path):
    """The verifier's plants on #100: a scheme test on a setting and on a parsed URL, outside the adapters."""
    estate = tmp_path / "kge" / "estate"
    estate.mkdir(parents=True)
    (estate / "x.py").write_text(
        "from urllib.parse import urlsplit\nfrom kge.pipeline.settings import settings\n\n\ndef f(url):\n"
        "    if settings().TMP_URL.startswith(\"gs://\"):\n        return 1\n"
        "    if urlsplit(url).scheme == \"gs\":\n        return 2\n"
        "    if url.startswith((\"s3://\", \"file://\")):\n        return 3\n")
    adapters = tmp_path / "kge" / "adapters"
    adapters.mkdir(parents=True)
    (adapters / "y.py").write_text("def g(url):\n    return url.startswith(\"gs://\")\n")
    assert scheme_branches(tmp_path / "kge") == ["kge/estate/x.py:6", "kge/estate/x.py:8", "kge/estate/x.py:10"]
