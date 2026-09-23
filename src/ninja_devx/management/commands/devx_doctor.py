from __future__ import annotations

import json
from dataclasses import asdict

from django.core.management.base import BaseCommand, CommandError, CommandParser

from ninja_devx.tooling.doctor import Finding, run_doctor

_SEVERITY_ORDER = {"info": 0, "warn": 1}


class Command(BaseCommand):
    help = (
        "Findings beyond manage.py check: unindexed query fields, unenforced owner/tenant "
        "fields, unhinted N+1 risks, missing pagination or permissions, and soft-deleted "
        "models with globally unique fields."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "target",
            nargs="?",
            help=(
                "A controller path (app.api.PostController) or a mounted route prefix "
                "(/v1/posts). Without it, check every controller of the configured APIs."
            ),
        )
        parser.add_argument("--json", action="store_true", help="emit JSON instead of a table")
        parser.add_argument(
            "--fail-on",
            choices=["info", "warn"],
            help="exit 1 if a finding at or above this severity is found",
        )

    def handle(self, *args: object, **options: object) -> None:
        target = str(options["target"]) if options.get("target") else None
        try:
            findings = run_doctor(target)
        except (ImportError, LookupError) as exc:
            raise CommandError(str(exc)) from exc
        if options.get("json"):
            self.stdout.write(json.dumps([asdict(finding) for finding in findings], indent=2))
        elif not findings:
            self.stdout.write(self.style.SUCCESS("No findings."))
        else:
            self._render(findings)
        threshold = options.get("fail_on")
        if isinstance(threshold, str):
            failing = [
                f for f in findings if _SEVERITY_ORDER[f.severity] >= _SEVERITY_ORDER[threshold]
            ]
            if failing:
                raise CommandError(f"{len(failing)} finding(s) at or above {threshold!r}")

    def _render(self, findings: list[Finding]) -> None:
        widths = (
            max(len(finding.severity) for finding in findings),
            max(len(finding.controller) for finding in findings),
        )
        for finding in findings:
            style = self.style.WARNING if finding.severity == "warn" else self.style.NOTICE
            line = (
                f"{finding.severity:<{widths[0]}}  {finding.controller:<{widths[1]}}  "
                f"{finding.message}"
            )
            self.stdout.write(style(line))
            self.stdout.write(f"{' ' * (widths[0] + widths[1] + 4)}-> {finding.hint}")
