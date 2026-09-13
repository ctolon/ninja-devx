"""Contract tests generated from the OpenAPI schema, with schemathesis.

Install the extra (``pip install ninja-devx[contract]``), then::

    import schemathesis

    @pytest.fixture
    def api_schema(ninja_contract, db):
        return ninja_contract(api)

    schema = schemathesis.pytest.from_fixture("api_schema")     # .include(path_regex=...) to filter

    @schema.parametrize()
    def test_api_contract(case):
        case.call_and_validate(headers={"Authorization": "Bearer test-token"})

Every operation gets property-based requests (valid and invalid input). Responses are
checked against the documented status codes, content types and schemas, so an
undocumented 500 or a body that doesn't match ``response=`` fails the test. Requests go
through Django's WSGI application in-process, inside the test database transaction.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from django.core.wsgi import get_wsgi_application
from ninja import NinjaAPI
from ninja.responses import NinjaJSONEncoder

if TYPE_CHECKING:
    from schemathesis.specs.openapi.schemas import OpenApiSchema

__all__ = ["contract_schema"]


def contract_schema(api: NinjaAPI, *, path_prefix: str | None = None) -> OpenApiSchema:
    """A schemathesis schema for ``api``, calling the Django WSGI app in-process.

    ``path_prefix`` defaults to where the API is mounted in the URLconf. Filter operations
    on the lazy schema: ``schemathesis.pytest.from_fixture("api_schema").include(...)``.

    :param api: The ``NinjaAPI`` to test.
    :param path_prefix: API root path (default: where it is mounted in the URLconf).
    """
    import schemathesis

    document = (
        api.get_openapi_schema()
        if path_prefix is None
        else api.get_openapi_schema(path_prefix=path_prefix)
    )
    # A JSON round trip turns Ninja's integer status keys into the strings OpenAPI requires.
    raw: dict[str, object] = json.loads(json.dumps(document, cls=NinjaJSONEncoder))
    schema = schemathesis.openapi.from_dict(raw)
    schema.app = get_wsgi_application()
    return schema
