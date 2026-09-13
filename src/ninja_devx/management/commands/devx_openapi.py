from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError, CommandParser

from ninja_devx.codegen import generate_python, generate_typescript, load_api


class Command(BaseCommand):
    help = "Export a NinjaAPI's OpenAPI schema, or generate a typed client from it."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("api", help="dotted path to a NinjaAPI, e.g. config.urls.api")
        parser.add_argument(
            "--format",
            choices=["json", "typescript", "python"],
            default="json",
            help="json (schema), typescript (types + fetch client) or python (httpx clients)",
        )
        parser.add_argument(
            "--python-style",
            choices=["pydantic", "typeddict"],
            default="pydantic",
            help="python models: pydantic (validated, with constraints) or typeddict",
        )
        parser.add_argument("--output", help="file to write (default: print)")
        parser.add_argument("--path-prefix", help="override the API root path (default: from urls)")
        parser.add_argument(
            "--check",
            action="store_true",
            help="fail if --output is missing or outdated (for CI)",
        )

    def handle(self, *args: object, **options: object) -> None:
        try:
            api = load_api(str(options["api"]))
        except (ImportError, TypeError) as exc:
            raise CommandError(str(exc)) from exc
        prefix = options["path_prefix"]
        document = api.get_openapi_schema(path_prefix=str(prefix) if prefix is not None else None)
        output_format = str(options["format"])
        try:
            if output_format == "typescript":
                content = generate_typescript(document)
            elif output_format == "python":
                if options["python_style"] == "typeddict":
                    content = generate_python(document, style="typeddict")
                else:
                    content = generate_python(document)
            else:
                content = json.dumps(document, indent=2, sort_keys=True, default=str) + "\n"
        except (ValueError, SyntaxError) as exc:
            raise CommandError(str(exc)) from exc

        output = options["output"]
        if not output:
            if options["check"]:
                raise CommandError("--check needs --output")
            self.stdout.write(content, ending="")
            return
        path = Path(str(output))
        if options["check"]:
            if not path.exists() or path.read_text() != content:
                raise CommandError(f"{path} is outdated; rerun without --check")
            self.stdout.write(self.style.SUCCESS(f"{path} is up to date"))
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        self.stdout.write(self.style.SUCCESS(f"Wrote {path}"))
