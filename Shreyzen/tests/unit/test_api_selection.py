"""
Unit tests for the API endpoint selection feature (Capability: api_select).

Pure-Python: no Locust, no live target — just the catalog + param resolution.
"""

import pytest

from load import catalog


# ── API_ENDPOINTS catalog ────────────────────────────────────────────────────

def test_api_endpoints_catalog_is_wellformed():
    assert catalog.API_ENDPOINTS, "catalog must not be empty"
    for key, ep in catalog.API_ENDPOINTS.items():
        assert ep.key == key
        assert ep.method in {"GET", "POST", "PUT", "PATCH", "DELETE"}
        assert ep.path.startswith("/")
        assert ep.weight >= 1
        # An id-based path must carry the [id] token, and vice-versa.
        assert ("[id]" in ep.path) == ep.needs_id


def test_api_select_scenario_registered_and_maps_to_user_class():
    assert "api_select" in catalog.SCENARIOS
    assert catalog.SCENARIOS["api_select"].user_class == "SelectiveApiUser"


# ── resolve_endpoints ────────────────────────────────────────────────────────

def test_resolve_endpoints_empty_returns_all():
    assert catalog.resolve_endpoints("") == list(catalog.API_ENDPOINTS)
    assert catalog.resolve_endpoints(None) == list(catalog.API_ENDPOINTS)
    assert catalog.resolve_endpoints([]) == list(catalog.API_ENDPOINTS)


def test_resolve_endpoints_accepts_csv_and_list():
    assert catalog.resolve_endpoints("create,read") == ["create", "read"]
    assert catalog.resolve_endpoints(["create", "read"]) == ["create", "read"]


def test_resolve_endpoints_dedupes_preserving_order():
    assert catalog.resolve_endpoints("read,create,read") == ["read", "create"]


def test_resolve_endpoints_rejects_unknown_key():
    with pytest.raises(ValueError, match="Unknown API endpoint 'bogus'"):
        catalog.resolve_endpoints("create,bogus")


# ── resolve_params plumbing ──────────────────────────────────────────────────

def test_resolve_params_threads_endpoints_for_api_select():
    p = catalog.resolve_params("api_select", "stress", endpoints="create,list")
    assert p.endpoints == ["create", "list"]
    assert "endpoints" in p.as_dict()


def test_resolve_params_ignores_endpoints_for_other_scenarios():
    # A non-selective scenario should never carry an endpoint filter.
    p = catalog.resolve_params("crud", "smoke", endpoints="create,list")
    assert p.endpoints == []


def test_resolve_params_api_select_defaults_to_all_endpoints():
    p = catalog.resolve_params("api_select", "smoke")
    assert p.endpoints == list(catalog.API_ENDPOINTS)


def test_resolve_params_api_select_validates_endpoints():
    with pytest.raises(ValueError, match="Unknown API endpoint"):
        catalog.resolve_params("api_select", "smoke", endpoints="nope")


def test_build_command_sets_endpoints_env(tmp_path):
    from load import engine
    p = catalog.resolve_params("api_select", "smoke", endpoints="create,read")
    _cmd, env = engine.build_command(p, tmp_path)
    assert env["SHREYZEN_ENDPOINTS"] == "create,read"


def test_build_command_omits_endpoints_env_for_crud(tmp_path):
    from load import engine
    p = catalog.resolve_params("crud", "smoke")
    _cmd, env = engine.build_command(p, tmp_path)
    assert "SHREYZEN_ENDPOINTS" not in env
