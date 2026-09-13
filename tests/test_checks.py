from django.core.management import call_command
from django.test import override_settings
from ninja import NinjaAPI, Schema

from ninja_devx import Container, mount
from ninja_devx.configuration.checks import check_controllers
from ninja_devx.crud import CRUDController, ModelService
from ninja_devx.layers import Repository
from tests.testapp.models import Note


class NoteOut(Schema):
    id: int
    text: str
    shout: str  # neither a field nor resolved


class NoteIn(Schema):
    text: str


class NeedsRepository(ModelService[Note]):
    def __init__(self, repository: Repository[Note]) -> None:
        super().__init__(repository)


class BrokenNotes(CRUDController[Note, NoteOut, NoteIn]):
    ordering_fields = ("priority", "missing")
    service_class = NeedsRepository


class FineOut(Schema):
    id: int
    text: str
    shout: str

    @staticmethod
    def resolve_shout(obj: Note) -> str:
        return obj.text.upper()


class FineNotes(CRUDController[Note, FineOut, NoteIn]):
    ordering_fields = ("priority",)
    search_fields = ("text", "owner__username")


broken_api = NinjaAPI(urls_namespace="broken-checks")
mount(broken_api, {"/notes": BrokenNotes})

fine_api = NinjaAPI(urls_namespace="fine-checks")
mount(fine_api, {"/notes": FineNotes}, container=Container())

not_an_api = object()


def ids(messages):
    return sorted(message.id for message in messages)


def test_checks_report_controller_problems():
    with override_settings(NINJA_DEVX={"CHECK_APIS": ["tests.test_checks.broken_api"]}):
        messages = check_controllers()
    assert ids(messages) == ["ninja_devx.E002", "ninja_devx.E004", "ninja_devx.W003"]
    by_id = {message.id: message.msg for message in messages}
    assert "has no field 'missing'" in by_id["ninja_devx.E002"]
    assert "NoteOut.shout" in by_id["ninja_devx.W003"]
    assert "needs repository" in by_id["ninja_devx.E004"]


def test_resolved_fields_and_containers_pass():
    with override_settings(NINJA_DEVX={"CHECK_APIS": ["tests.test_checks.fine_api"]}):
        assert check_controllers() == []


def test_bad_check_apis_entries():
    with override_settings(
        NINJA_DEVX={"CHECK_APIS": ["tests.test_checks.not_an_api", "tests.nowhere.api"]}
    ):
        messages = check_controllers()
    assert ids(messages) == ["ninja_devx.E001", "ninja_devx.E001"]


def test_plugins_and_overrides_add_messages():
    from django.core.checks import Warning as CheckWarning

    class Audit:
        def on_operation(self, controller, name, spec):
            return spec

        def bindings(self, controller, name, spec):
            return ()

        def checks(self, controller):
            return [CheckWarning(f"{controller.__name__} audited", id="audit.W001")]

    class Audited(FineNotes):
        @classmethod
        def checks(cls, container=None):
            return [*super().checks(container), CheckWarning("custom", id="audit.W002")]

    api = NinjaAPI(urls_namespace="audited-checks")
    mount(api, {"/audited": Audited}, plugins=[Audit()])
    globals()["audited_api"] = api
    with override_settings(NINJA_DEVX={"CHECK_APIS": ["tests.test_checks.audited_api"]}):
        assert ids(check_controllers()) == ["audit.W001", "audit.W002"]


def test_without_check_apis_every_built_router_is_checked(capsys):
    messages = check_controllers()
    assert "ninja_devx.E002" in ids(messages)  # BrokenNotes above
    call_command("check", "--tag", "ninja_devx", fail_level="CRITICAL")
