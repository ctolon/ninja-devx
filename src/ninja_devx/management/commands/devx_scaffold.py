from __future__ import annotations

from pathlib import Path
from typing import cast

from django.apps import apps
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db.models import Model

from ninja_devx.tooling.scaffold import ScaffoldOptions, render_resource, render_tests, snake_case


class Command(BaseCommand):
    help = "Generate typed schemas, a CRUD controller and tests for a model."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("model", nargs="?", help="app_label.ModelName, e.g. blog.Article")
        parser.add_argument(
            "--check",
            action="store_true",
            help="report drift between models and the schemas of mounted controllers",
        )
        parser.add_argument("--output", help="module file (default: <app>/api/<model>.py)")
        parser.add_argument("--tests", help="test file (default: <app>/tests/test_<model>_api.py)")
        parser.add_argument("--read", nargs="+", help="fields of the output schema")
        parser.add_argument("--write", nargs="+", help="fields of the input schema")
        parser.add_argument("--owner", help="foreign key to the user (sets owner_field)")
        parser.add_argument(
            "--async", action="store_true", dest="asynchronous", help="generate async operations"
        )
        parser.add_argument("--no-tests", action="store_true", help="do not write the test file")
        parser.add_argument("--force", action="store_true", help="overwrite existing files")
        parser.add_argument(
            "--print", action="store_true", dest="print_only", help="print instead of writing"
        )

    def handle(self, *args: object, **options: object) -> None:
        if options["check"]:
            self._check(options["model"], int(str(options.get("verbosity", 1))))
            return
        if not options["model"]:
            raise CommandError("Give a model (app_label.ModelName) or --check")
        label = str(options["model"])
        try:
            model = apps.get_model(label)
        except (LookupError, ValueError) as exc:
            raise CommandError(f"Unknown model {label!r}; use app_label.ModelName") from exc

        config = apps.get_app_config(model._meta.app_label)
        snake = snake_case(model.__name__)
        output = Path(str(options["output"] or Path(config.path) / "api" / f"{snake}.py"))
        tests = Path(str(options["tests"] or Path(config.path) / "tests" / f"test_{snake}_api.py"))
        scaffold = ScaffoldOptions(
            read_fields=_names(options["read"]),
            write_fields=_names(options["write"]),
            owner_field=str(options["owner"]) if options["owner"] else None,
            asynchronous=bool(options["asynchronous"]),
        )
        try:
            resource = render_resource(model, scaffold)
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        module = _module_name(output, Path(config.path), config.name)
        files = {output: resource}
        if not options["no_tests"]:
            files[tests] = render_tests(model, module, scaffold)

        if options["print_only"]:
            for path, content in files.items():
                self.stdout.write(f"# --- {path}\n{content}")
            return
        if existing := [str(path) for path in files if path.exists() and not options["force"]]:
            raise CommandError(f"Refusing to overwrite {existing}; pass --force")
        for path, content in files.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            init = path.parent / "__init__.py"
            if not init.exists():
                init.write_text("")
            path.write_text(content)
            self.stdout.write(self.style.SUCCESS(f"Wrote {path}"))
        self.stdout.write(
            f'Mount it: api.add_router("/{snake}s", {model.__name__}Controller.as_router())'
        )

    def _check(self, label: object, verbosity: int) -> None:
        from django.urls import get_resolver

        from ninja_devx.crud.controllers import ModelController
        from ninja_devx.routing.controller import built_routers
        from ninja_devx.tooling.drift import schema_drift

        get_resolver().url_patterns  # noqa: B018 - imports the URLconf (and the APIs)
        wanted = apps.get_model(str(label)) if label else None
        seen: set[type[object]] = set()
        errors = 0
        for entry in built_routers():
            if entry.controller in seen or not issubclass(entry.controller, ModelController):
                continue
            controller = cast("type[ModelController[Model]]", entry.controller)
            seen.add(controller)
            if wanted is not None and controller.get_model() is not wanted:
                continue
            for drift in schema_drift(controller):
                if drift.severity == "info" and verbosity < 2:
                    continue
                errors += drift.severity == "error"
                style = self.style.ERROR if drift.severity == "error" else self.style.WARNING
                self.stdout.write(style(str(drift)))
        if errors:
            raise CommandError(f"{errors} schema drift error(s)")
        self.stdout.write(self.style.SUCCESS(f"No schema drift in {len(seen)} controller(s)"))


def _names(value: object) -> list[str] | None:
    return [str(item) for item in cast("list[object]", value)] if isinstance(value, list) else None


def _module_name(path: Path, app_path: Path, app_module: str) -> str:
    try:
        relative = path.resolve().relative_to(app_path.resolve().parent)
    except ValueError:
        return path.stem
    base = app_module.rsplit(".", 1)[0] + "." if "." in app_module else ""
    return base + ".".join(relative.with_suffix("").parts)
