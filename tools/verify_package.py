"""Install a fresh wheel with each extra in isolated environments; write JSON evidence.

Run after ``uv build --out-dir /tmp/devx-dist``. No repository or CI configuration is
changed. Install subprocesses have deadlines and use the supplied wheel, never src/.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SMOKE = r"""
import importlib
import importlib.metadata
import json
import sys
from pathlib import Path
import ninja_devx
assert ninja_devx.__version__ == sys.argv[2]
assert Path(ninja_devx.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
from django.conf import settings
settings.configure(
    SECRET_KEY="isolated-package-test", ROOT_URLCONF=__name__, USE_TZ=True,
    INSTALLED_APPS=["django.contrib.auth", "django.contrib.contenttypes", "ninja_devx",
                    "ninja_devx.contrib.grants", "ninja_devx.contrib.apikeys",
                    "ninja_devx.contrib.audit", "ninja_devx.contrib.webhooks",
                    "ninja_devx.contrib.jobs"],
    DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
)
import django
django.setup()
from django.core.management import call_command
call_command("migrate", verbosity=0)
from ninja import Schema
from ninja.testing import TestClient
from ninja_devx import Controller, get
class Output(Schema):
    ok: bool
class Example(Controller):
    @get("/", response=Output)
    def index(self, request):
        return {"ok": True}
assert TestClient(Example.as_router()).get("/").json() == {"ok": True}
for name in ninja_devx.__all__:
    getattr(ninja_devx, name)
from ninja_devx.models import IdempotencyRecord, UploadRecord
assert IdempotencyRecord.objects.count() == 0
assert UploadRecord.objects.count() == 0
selected = sys.argv[1].split(",") if sys.argv[1] else []
modules = {
    "dishka": ["dishka", "ninja_devx.contrib.dishka"],
    "svcs": ["svcs", "ninja_devx.contrib.svcs"],
    "otel": ["opentelemetry.trace", "ninja_devx.contrib.otel"],
    "client": ["httpx"], "contract": ["schemathesis", "ninja_devx.testing.contracts"],
    "guardian": ["guardian"], "orjson": ["orjson"], "msgspec": ["msgspec"],
    "s3": ["boto3", "ninja_devx.contrib.uploads"], "crypto": ["cryptography.fernet"],
    "redis": ["redis", "ninja_devx.contrib.redis_throttle"],
    "structlog": ["structlog", "ninja_devx.http.requestlog"],
    "zeal": ["zeal", "ninja_devx.contrib.nplusone"],
}
for extra in selected:
    for module in modules[extra]:
        importlib.import_module(module)
if "crypto" in selected:
    from django.test import override_settings
    from ninja_devx.contrib.webhooks.secrets import generate_key, encrypt_secret, decrypt_secret
    with override_settings(NINJA_DEVX={"WEBHOOK_SECRET_KEYS": [generate_key()]}):
        assert decrypt_secret(encrypt_secret("test-only")) == "test-only"
if "orjson" in selected or "msgspec" in selected:
    from django.test import RequestFactory
    from ninja_devx.serialization.renderers import ORJSONRenderer, MsgspecRenderer
    for extra, renderer in [("orjson", ORJSONRenderer), ("msgspec", MsgspecRenderer)]:
        if extra in selected:
            rendered = renderer().render(
                RequestFactory().get("/"), {"ok": True}, response_status=200
            )
            assert json.loads(rendered) == {"ok": True}
print(json.dumps({"python": sys.version.split()[0], "django": django.get_version(),
                  "ninja": importlib.metadata.version("django-ninja"), "extras": selected}))
"""


def run(command: list[str], *, cwd: Path, env: dict[str, str], timeout: int = 180) -> str:
    result = subprocess.run(
        command, cwd=cwd, env=env, text=True, capture_output=True, timeout=timeout, check=False
    )
    if result.returncode:
        raise RuntimeError(
            f"{command[0]} exited {result.returncode}:\n{result.stdout}\n{result.stderr}"
        )
    return result.stdout


def verify(wheel: Path, extras: str, version: str) -> dict[str, object]:
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("DJANGO_SETTINGS_MODULE", None)
    env["UV_NO_PROGRESS"] = "1"
    with tempfile.TemporaryDirectory(prefix="devx-extra-") as directory:
        root = Path(directory)
        python = root / "venv" / "bin" / "python"
        run(["uv", "venv", "--python", sys.executable, str(root / "venv")], cwd=root, env=env)
        requirement = str(wheel) + (f"[{extras}]" if extras else "")
        run(["uv", "pip", "install", "--python", str(python), requirement], cwd=root, env=env)
        output = run([str(python), "-c", SMOKE, extras, version], cwd=root, env=env, timeout=60)
        return json.loads(output.strip().splitlines()[-1])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()
    if args.workers <= 0:
        parser.error("--workers must be positive")
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    wheel = args.wheel.resolve()
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        for required in [
            "ninja_devx/py.typed",
            "ninja_devx/migrations/0002_uploadrecord.py",
            "ninja_devx/templates/project/pyproject.toml",
            "ninja_devx/templates/project/manage.py-tpl",
            "ninja_devx/templates/app_template/models.py-tpl",
        ]:
            if required not in names:
                raise RuntimeError(f"wheel is missing {required}")
    extras = list(metadata["optional-dependencies"])
    cases = ["", *extras, ",".join(extras)]
    results: dict[str, object] = {}
    failures = 0
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(verify, wheel, extra, metadata["version"]): extra for extra in cases
        }
        for future in as_completed(futures):
            label = futures[future] or "core"
            try:
                results[label] = future.result()
                print(f"PASS {label}", flush=True)
            except Exception as exc:
                failures += 1
                results[label] = {"error": str(exc)}
                print(f"FAIL {label}: {exc}", flush=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(results, indent=2) + "\n")
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
