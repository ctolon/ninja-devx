"""Every operation of the tracker, fuzzed by schemathesis from the OpenAPI schema."""

from collections.abc import Callable

import pytest
import schemathesis
from hypothesis import HealthCheck, settings
from ninja import NinjaAPI
from schemathesis.specs.openapi.checks import negative_data_rejection

from config.urls import api
from tracker.models import Workspace

schema = schemathesis.pytest.from_fixture("api_schema")


@pytest.fixture
def api_schema(ninja_contract: Callable[[NinjaAPI], object], acme: Workspace) -> object:
    return ninja_contract(api)


@schema.parametrize()
@settings(max_examples=15, suppress_health_check=list(HealthCheck), deadline=None)
def test_api_contract(case: schemathesis.Case) -> None:  # type: ignore[type-arg]
    case.call_and_validate(
        headers={"Authorization": "Bearer alice", "X-Workspace": "acme"},
        # Ninja ignores unknown query parameters, which this check reports as accepted.
        excluded_checks=[negative_data_rejection],  # type: ignore[list-item]
    )
