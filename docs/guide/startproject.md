# Starting a project

`manage.py devx_startproject` writes a runnable project: settings, a mounted API and a
first app, instead of assembling them by hand. Like `devx_startapp`, it runs from the
`manage.py` of any Django project with `"ninja_devx"` in `INSTALLED_APPS`, and writes the
new project into a separate directory.

```bash
python manage.py devx_startproject acme
cd acme
python manage.py migrate
python manage.py runserver   # http://127.0.0.1:8000/api/docs
pytest -q
```

`acme/` contains:

| Path | Purpose |
|---|---|
| `config/settings.py` | `INSTALLED_APPS` with the `contrib` apps commented, `NINJA_DEVX`, JSON console logging, database from `DATABASE_URL` (SQLite by default) |
| `config/api.py` | the `NinjaAPI`, request id/security header/hardening middleware through `ninja_devx.plugins.install`, `ErrorMap.django_defaults()`, and the mounted health check |
| `config/urls.py`, `config/asgi.py`, `config/wsgi.py` | the usual entry points |
| `pyproject.toml` | dependencies, `ninja-devx` pinned to the version that generated the project |
| `compose.yaml`, `.env.example` | a local PostgreSQL matching `DATABASE_URL` |
| `tests/conftest.py`, `tests/test_health.py` | a `user` fixture and a passing test using `ninja_client` (ninja-devx's own pytest plugin) |

## Options

```bash
python manage.py devx_startproject acme --app blog     # also runs devx_startapp
python manage.py devx_startproject acme --no-docker     # skip compose.yaml
python manage.py devx_startproject acme path/to/dir     # write into an existing empty directory
```

`--app` scaffolds a first app with `devx_startapp`, adds it to `INSTALLED_APPS`, and
mounts its controller in `config/api.py` next to the health check.

Generating into a directory that already has files in it is refused; an empty directory,
or no directory (the project name is used under the current one), is fine to reuse.

## What is not generated

Authentication, permissions, tenancy and the `contrib` apps (API keys, audit, object
permissions, webhooks) are one `INSTALLED_APPS` line away, commented in
`config/settings.py`. See the [guides index](index.md) for each.
