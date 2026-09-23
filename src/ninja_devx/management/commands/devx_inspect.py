from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError, CommandParser

from ninja_devx.tooling.inspect import as_dict, inspect_target, render


class Command(BaseCommand):
    help = (
        "Show the resolved policy of mounted controllers: scoping, permissions, "
        "transaction, pagination, relations and operations."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "target",
            nargs="?",
            help=(
                "A controller path (app.api.PostController) or a mounted route prefix "
                "(/v1/posts). Without it, inspect every controller of the configured APIs."
            ),
        )
        parser.add_argument("--json", action="store_true", help="emit JSON instead of a tree")

    def handle(self, *args: object, **options: object) -> None:
        target = str(options["target"]) if options.get("target") else None
        try:
            results = inspect_target(target)
        except (ImportError, LookupError) as exc:
            raise CommandError(str(exc)) from exc
        if not results:
            if target is None:
                raise CommandError("No controller is mounted; check NINJA_DEVX['CHECK_APIS']")
            raise CommandError(f"No mounted controller matches {target!r}")
        if options.get("json"):
            self.stdout.write(json.dumps(as_dict(results), indent=2, default=str))
            return
        self.stdout.write("\n\n".join(render(item) for item in results))
