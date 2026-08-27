from importlib.util import find_spec


def module_exists(module_name: str) -> bool:
    try:
        return find_spec(module_name) is not None
    except ModuleNotFoundError:
        return False


def test_agent_run_kernel_has_one_canonical_import_surface() -> None:
    # Given the repository itself owns the Agent harness
    module_name = "ads_booster.agent.runs"

    # When an installed consumer resolves durable run APIs
    exists = module_exists(module_name)

    # Then the Agent package owns that public surface
    assert exists


def test_trace_v1_connector_has_a_versioned_import_surface() -> None:
    # Given Trace is a versioned connector plugin in the same distribution
    module_name = "ads_booster.connectors.trace.v1"

    # When an installed consumer resolves Trace version one
    exists = module_exists(module_name)

    # Then the versioned connector package is discoverable
    assert exists
