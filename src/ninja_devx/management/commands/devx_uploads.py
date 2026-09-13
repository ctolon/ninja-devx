from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.utils.module_loading import import_string

from ...contrib.uploads import UploadController


class Command(BaseCommand):
    help = "Clean expired pending uploads for a configured UploadController."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("controller", help="import path to the configured UploadController")
        parser.add_argument(
            "--limit", type=int, default=100, help="maximum pending records per run"
        )

    def handle(self, *args: object, **options: object) -> None:
        limit = int(str(options["limit"]))
        if limit <= 0:
            raise CommandError("--limit must be positive")
        try:
            controller: object = import_string(str(options["controller"]))
        except (ImportError, AttributeError) as exc:
            raise CommandError(str(exc)) from exc
        if not isinstance(controller, type) or not issubclass(controller, UploadController):
            raise CommandError("controller must be an UploadController subclass")
        cleaned = controller().cleanup_expired(limit=limit)
        self.stdout.write(json.dumps({"cleaned": cleaned}))
