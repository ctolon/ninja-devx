from __future__ import annotations

from argparse import ArgumentParser
from pathlib import Path

from django.core.management.commands.startapp import Command as StartAppCommand

import ninja_devx
from ninja_devx.tooling.startproject import target_directory

TEMPLATE = Path(ninja_devx.__file__).parent / "templates" / "app_template"


class Command(StartAppCommand):
    help = (
        "Create a Django app laid out for ninja-devx: a controller, schemas, a service "
        "and tests (Django's startapp with the ninja-devx template)."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        super().add_arguments(parser)
        parser.set_defaults(template=str(TEMPLATE))

    def handle(self, **options: object) -> None:  # type: ignore[override]  # same as startapp
        directory = options.get("directory")
        if directory:
            with target_directory(Path(str(directory)).expanduser().resolve()):
                super().handle(**options)
        else:
            super().handle(**options)
        name = str(options["name"])
        camel = "".join(part.capitalize() for part in name.split("_"))
        self.stdout.write(
            self.style.SUCCESS(f"Created {name}.") + "\nNext steps:\n"
            f'  1. INSTALLED_APPS += ["{name}"]\n'
            f'  2. mount(api, {{"/{name}": {camel}Controller}}, container=Container())  '
            f"# from {name}.api import {camel}Controller\n"
            f"  3. Add a model, then: manage.py devx_scaffold {name}.<Model>\n"
        )
