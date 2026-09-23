from __future__ import annotations

from datetime import timedelta
from typing import cast

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.utils import timezone

from ninja_devx.contrib.jobs.models import Job
from ninja_devx.contrib.jobs.runner import run_job


def _as_dict(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return cast("dict[str, object]", value)
    return {}


def _as_list(value: object) -> list[object]:
    if isinstance(value, list):
        return cast("list[object]", value)
    return []


class Command(BaseCommand):
    help = (
        "prune --older-than DAYS: delete terminal jobs older than this. "
        "retry <id>: replay a failed or cancelled job synchronously."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("action", choices=["prune", "retry"], help="what to do")
        parser.add_argument("id", nargs="?", help="job id (retry)")
        parser.add_argument(
            "--older-than", type=int, dest="older_than", metavar="DAYS", help="prune threshold"
        )
        parser.add_argument(
            "--retry-count", type=int, default=0, help="extra attempts on failure (retry)"
        )

    def handle(self, *args: object, **options: object) -> None:
        if options["action"] == "prune":
            days = options["older_than"]
            if not isinstance(days, int) or days < 1:
                raise CommandError("prune requires --older-than DAYS")
            cutoff = timezone.now() - timedelta(days=days)
            removed, _details = Job.objects.filter(
                status__in=[Job.Status.SUCCEEDED, Job.Status.FAILED, Job.Status.CANCELLED],
                created__lt=cutoff,
            ).delete()
            self.stdout.write(f"removed={removed}")
            return
        job_id = options["id"]
        if not job_id:
            raise CommandError("retry requires an id")
        try:
            row = Job.objects.get(pk=str(job_id))
        except (Job.DoesNotExist, ValueError, TypeError) as exc:
            raise CommandError("No job with that id") from exc
        if row.status not in {Job.Status.FAILED, Job.Status.CANCELLED}:
            raise CommandError("Only a failed or cancelled job can be retried")
        arguments = _as_dict(row.arguments)
        call_args = _as_list(arguments.get("args"))
        call_kwargs = _as_dict(arguments.get("kwargs"))
        retry_count = int(str(options["retry_count"]))
        run_job(str(row.pk), row.function, retry_count, *call_args, **call_kwargs)
        self.stdout.write(self.style.SUCCESS(f"Retried {row.pk}"))
