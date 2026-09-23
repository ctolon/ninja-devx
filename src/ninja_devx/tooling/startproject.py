"""Post-render edits for ``manage.py devx_startproject`` that Django's template engine
cannot express as a plain variable substitution: wiring a first app into the generated
settings and API module, and dropping the Docker files for ``--no-docker``.

Plain string edits over the rendered project, not a second templating pass::

    wire_app(directory, "blog")   # after ``call_command("devx_startapp", "blog", ...)``
    drop_docker(directory)        # deletes compose.yaml and its README section
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["camel_case", "drop_docker", "wire_app"]

INSTALLED_APPS_ANCHOR = '"ninja_devx",  # controllers, CRUD, permissions and DI'
IMPORT_ANCHOR = "from ninja_devx.plugins import APIPlugin, install"
MOUNT_ANCHOR = 'mount(api, {"/health": HealthController}, container=container)'
DOCKER_SECTION_HEADING = "\n## Local PostgreSQL"


def camel_case(name: str) -> str:
    """``blog_posts`` -> ``BlogPosts``, matching ``devx_startapp``'s controller names."""
    return "".join(part.capitalize() for part in name.split("_"))


def wire_app(directory: Path, app_name: str) -> None:
    """Add ``app_name`` to ``INSTALLED_APPS`` and mount its controller in ``config/api.py``.

    Call after ``devx_startapp`` has written ``app_name`` under ``directory``.

    :param directory: The generated project's root.
    :param app_name: The app just scaffolded by ``devx_startapp``.
    """
    controller = f"{camel_case(app_name)}Controller"

    settings_path = directory / "config" / "settings.py"
    settings_source = settings_path.read_text()
    if INSTALLED_APPS_ANCHOR not in settings_source:
        raise ValueError(f"{settings_path} does not match the generated template")
    settings_path.write_text(
        settings_source.replace(
            INSTALLED_APPS_ANCHOR, f'{INSTALLED_APPS_ANCHOR}\n    "{app_name}",', 1
        )
    )

    api_path = directory / "config" / "api.py"
    api_source = api_path.read_text()
    if IMPORT_ANCHOR not in api_source or MOUNT_ANCHOR not in api_source:
        raise ValueError(f"{api_path} does not match the generated template")
    api_source = api_source.replace(
        IMPORT_ANCHOR, f"{IMPORT_ANCHOR}\n\nfrom {app_name}.api import {controller}", 1
    )
    api_source = api_source.replace(
        MOUNT_ANCHOR,
        f'mount(api, {{"/health": HealthController, "/{app_name}": {controller}}}, '
        f"container=container)",
        1,
    )
    api_path.write_text(api_source)


def drop_docker(directory: Path) -> None:
    """Remove ``compose.yaml`` and the matching section of ``README.md`` for ``--no-docker``.

    :param directory: The generated project's root.
    """
    (directory / "compose.yaml").unlink(missing_ok=True)
    readme_path = directory / "README.md"
    readme_source = readme_path.read_text()
    heading_at = readme_source.find(DOCKER_SECTION_HEADING)
    if heading_at != -1:
        readme_path.write_text(readme_source[:heading_at].rstrip() + "\n")
