import os
import subprocess
import sys

import pytest
from django.core.management import CommandError, call_command

import ninja_devx


def _env() -> dict[str, str]:
    """The parent test process sets ``DJANGO_SETTINGS_MODULE=tests.settings``; the
    generated project needs its own, and no other ninja-devx test env leaking in."""
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("DJANGO_", "TEST_", "NINJA_DEVX"))
    }
    env["DJANGO_SETTINGS_MODULE"] = "config.settings"
    return env


def _run(*args: str, cwd: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "manage.py", *args],
        cwd=cwd,
        env=_env(),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def _pytest(cwd: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=cwd,
        env=_env(),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_generated_project_checks_and_migrates(tmp_path):
    target = tmp_path / "acme"
    call_command("devx_startproject", "acme", str(target))

    for path in (
        "manage.py",
        "config/settings.py",
        "config/urls.py",
        "config/api.py",
        "config/asgi.py",
        "config/wsgi.py",
        "pyproject.toml",
        ".gitignore",
        ".env.example",
        "compose.yaml",
        "README.md",
        "tests/conftest.py",
    ):
        assert (target / path).exists(), path

    settings = (target / "config" / "settings.py").read_text()
    assert 'NINJA_DEVX = {"CHECK_APIS": ["config.api.api"]}' in settings
    assert '"ninja_devx",  # controllers, CRUD, permissions and DI' in settings
    assert '# "ninja_devx.contrib.apikeys"' in settings

    api = (target / "config" / "api.py").read_text()
    assert "install(api, [Hardening()])" in api
    assert 'mount(api, {"/health": HealthController}, container=container)' in api
    assert "ErrorMap.django_defaults()" in api

    pyproject = (target / "pyproject.toml").read_text()
    assert f'"ninja-devx=={ninja_devx.__version__}"' in pyproject

    checked = _run("check", "--fail-level", "WARNING", cwd=str(target))
    assert checked.returncode == 0, checked.stdout + checked.stderr

    migrated = _run("migrate", cwd=str(target))
    assert migrated.returncode == 0, migrated.stdout + migrated.stderr
    assert (target / "db.sqlite3").exists()

    tested = _pytest(str(target))
    assert tested.returncode == 0, tested.stdout + tested.stderr


def test_refuses_to_overwrite_a_non_empty_directory(tmp_path):
    target = tmp_path / "acme"
    target.mkdir()
    (target / "keepme.txt").write_text("hello")

    with pytest.raises(CommandError, match="not empty"):
        call_command("devx_startproject", "acme", str(target))


def test_generating_into_an_existing_empty_directory_is_allowed(tmp_path):
    target = tmp_path / "acme"
    target.mkdir()

    call_command("devx_startproject", "acme", str(target))

    assert (target / "manage.py").exists()


def test_an_invalid_name_leaves_no_directory_behind(tmp_path):
    target = tmp_path / "acme"

    with pytest.raises(CommandError):
        call_command("devx_startproject", "not-an-identifier", str(target))

    assert not target.exists()


def test_no_docker_drops_the_compose_file_and_readme_section(tmp_path):
    target = tmp_path / "acme"
    call_command("devx_startproject", "acme", str(target), no_docker=True)

    assert not (target / "compose.yaml").exists()
    assert "Local PostgreSQL" not in (target / "README.md").read_text()


def test_app_option_scaffolds_and_mounts_a_first_app(tmp_path):
    target = tmp_path / "acme"
    call_command("devx_startproject", "acme", str(target), app="blog")

    assert (target / "blog" / "models.py").exists()

    settings = (target / "config" / "settings.py").read_text()
    assert '"blog",' in settings

    api = (target / "config" / "api.py").read_text()
    assert "from blog.api import BlogController" in api
    assert (
        'mount(api, {"/health": HealthController, "/blog": BlogController}, '
        "container=container)" in api
    )

    checked = _run("check", "--fail-level", "WARNING", cwd=str(target))
    assert checked.returncode == 0, checked.stdout + checked.stderr

    migrated = _run("migrate", cwd=str(target))
    assert migrated.returncode == 0, migrated.stdout + migrated.stderr
