from __future__ import annotations

from argparse import ArgumentParser
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import CommandError
from django.core.management.commands.startproject import Command as StartProjectCommand

import ninja_devx
from ninja_devx.tooling.startproject import camel_case, drop_docker, wire_app

TEMPLATE = Path(ninja_devx.__file__).parent / "templates" / "project"


class Command(StartProjectCommand):
    help = (
        "Create a runnable ninja-devx project: settings wired with request id, security "
        "header and hardening middleware, a health check, JSON logging, a database from "
        "DATABASE_URL, and a Postgres compose file (Django's startproject with a template)."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        super().add_arguments(parser)
        parser.add_argument(
            "--no-docker", action="store_true", help="skip compose.yaml (no local Postgres)"
        )
        parser.add_argument(
            "--app", metavar="NAME", help="also scaffold a first app with devx_startapp"
        )
        parser.set_defaults(
            template=str(TEMPLATE),
            extensions=["py", "toml", "md", "yaml", "gitignore", "env.example"],
        )

    def handle(self, **options: object) -> None:  # type: ignore[override]  # same as startapp
        name = str(options["name"])
        no_docker = bool(options.pop("no_docker"))
        app_option = options.pop("app")
        app_name = str(app_option) if app_option else None
        directory = options.get("directory")
        top_dir = Path(str(directory)).expanduser().resolve() if directory else Path.cwd() / name
        if top_dir.exists() and any(top_dir.iterdir()):
            raise CommandError(f"Refusing to overwrite {top_dir}: it is not empty")

        options["directory"] = str(top_dir)
        options["ninja_devx_version"] = ninja_devx.__version__
        super().handle(**options)

        if no_docker:
            drop_docker(top_dir)
        if app_name:
            call_command("devx_startapp", app_name, str(top_dir / app_name))
            wire_app(top_dir, app_name)

        self.stdout.write(self.style.SUCCESS(f"Created {name} in {top_dir}.") + "\nNext steps:\n")
        self.stdout.write(f"  1. cd {top_dir}\n  2. python manage.py migrate\n")
        if app_name:
            camel = camel_case(app_name)
            self.stdout.write(
                f"  3. Add a model to {app_name}/models.py, then: "
                f"manage.py devx_scaffold {app_name}.<Model>\n"
                f"     ({app_name}/api.py already has {camel}Controller mounted "
                f"at /api/{app_name})\n"
            )
        else:
            self.stdout.write("  3. manage.py devx_startapp <app> to add your first app\n")
