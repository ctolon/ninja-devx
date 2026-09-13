import importlib
import sys
from io import StringIO

from django.core.management import call_command


def test_startapp_creates_a_working_app(tmp_path, monkeypatch):
    target = tmp_path / "devx_billing"
    target.mkdir()
    out = StringIO()
    call_command("devx_startapp", "devx_billing", str(target), stdout=out)
    assert "devx_scaffold devx_billing.<Model>" in out.getvalue()
    files = {path.relative_to(target).as_posix() for path in target.rglob("*.py")}
    expected = {"api.py", "apps.py", "schemas.py", "services.py", "tests/test_api.py"}
    assert expected <= files
    assert "class DevxBillingController(Controller)" in (target / "api.py").read_text()

    # the generated test passes against the generated app
    monkeypatch.syspath_prepend(str(tmp_path))
    try:
        generated = importlib.import_module("devx_billing.tests.test_api")
        generated.test_status()
    finally:
        for name in [name for name in sys.modules if name.startswith("devx_billing")]:
            del sys.modules[name]
