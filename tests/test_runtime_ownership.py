import gc
import weakref

from django.test import override_settings
from ninja import Router

from ninja_devx import Controller, get
from ninja_devx._internal.cache import owned_cache
from ninja_devx.configuration.runtime import RuntimeState, runtime_state
from ninja_devx.configuration.settings import get_settings
from ninja_devx.routing.controller import built_router, built_routers


def test_class_cache_is_not_inherited_and_does_not_keep_class_alive():
    class Parent:
        pass

    parent: dict[str, object] = owned_cache(Parent, "example")
    parent["value"] = Parent

    class Child(Parent):
        pass

    child: dict[str, object] = owned_cache(Child, "example")
    assert child == {}
    child["value"] = Child
    reference = weakref.ref(Child)
    del Child, child
    gc.collect()
    assert reference() is None
    assert parent["value"] is Parent


def test_settings_cache_belongs_to_app_and_observes_overrides():
    state = runtime_state()
    assert state is not None
    original = get_settings().bulk_limit
    assert state.settings is get_settings()
    assert RuntimeState().settings is None
    with override_settings(NINJA_DEVX={"BULK_LIMIT": 17}):
        assert get_settings().bulk_limit == 17
        assert state.settings is get_settings()
    assert get_settings().bulk_limit == original


def test_router_metadata_is_owned_and_registry_drops_dead_routers():
    class Example(Controller):
        @get("/")
        def index(self, request):
            return {"ok": True}

    router = Example.as_router()
    metadata = built_router(router)
    assert metadata is not None
    assert metadata.controller is Example
    assert metadata in built_routers()
    assert built_router(Router()) is None
    reference = weakref.ref(router)
    del router
    gc.collect()
    assert reference() is None
    assert metadata not in built_routers()
