"""Model constants and platform operations share one vocabulary without widening role grants."""

from kge.pipeline.access import process_vocabulary


def test_platform_operations_extend_model_constants_without_mutating_them():
    constants = {"querying": "Query", "future_process": "Future", "drafting": "Model drafting"}
    processes = process_vocabulary(constants)
    assert list(processes) == ["querying", "future_process", "drafting", "building", "improving", "accepting"]
    assert processes["drafting"] == "Model drafting"
    assert all(isinstance(description, str) and description for description in processes.values())
    assert constants == {"querying": "Query", "future_process": "Future", "drafting": "Model drafting"}
    assert process_vocabulary(processes) == processes


def test_platform_operations_exist_without_model_constants():
    assert set(process_vocabulary()) == {"drafting", "building", "improving", "accepting"}
