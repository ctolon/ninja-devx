from __future__ import annotations

import json
import time

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import router, transaction
from django.db.models import Count, Min
from django.utils import timezone

from ninja_devx.contrib.webhooks.maintenance import prune_events, retry_deliveries
from ninja_devx.contrib.webhooks.models import WebhookDelivery, WebhookEndpoint
from ninja_devx.contrib.webhooks.network import URLPolicy
from ninja_devx.contrib.webhooks.outbox import deliver_due
from ninja_devx.contrib.webhooks.secrets import generate_key, reencrypt_secret


class Command(BaseCommand):
    help = (
        "deliver: send due webhook deliveries (once, or continuously with --loop). "
        "generate-key: print a new WEBHOOK_SECRET_KEYS key. "
        "encrypt-secrets: encrypt stored secrets with the first key (also after rotation)."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "action",
            choices=["deliver", "stats", "prune", "retry", "generate-key", "encrypt-secrets"],
            help="what to do",
        )
        parser.add_argument("--database", default=None, help="outbox database alias")
        parser.add_argument(
            "--days", type=int, default=30, help="retain terminal events this many days"
        )
        parser.add_argument("--delivery-id", type=int, help="one unclaimed delivery to replay")
        parser.add_argument("--limit", type=int, default=100, help="deliveries per batch")
        parser.add_argument("--timeout", type=float, default=10.0, help="seconds per request")
        parser.add_argument("--loop", action="store_true", help="keep polling")
        parser.add_argument("--interval", type=float, default=2.0, help="seconds between polls")
        parser.add_argument(
            "--allow-http", action="store_true", help="also send to http:// URLs (development)"
        )
        parser.add_argument(
            "--allow-private-networks",
            action="store_true",
            help="also send to loopback and private addresses (development)",
        )

    def handle(self, *args: object, **options: object) -> None:
        database = str(options["database"] or router.db_for_write(WebhookDelivery))
        if options["action"] == "generate-key":
            self.stdout.write(generate_key())
            return
        if options["action"] == "encrypt-secrets":
            self._encrypt_secrets(database)
            return
        limit = int(str(options["limit"]))
        timeout = float(str(options["timeout"]))
        interval = float(str(options["interval"]))
        if limit <= 0 or timeout <= 0 or interval <= 0:
            raise CommandError("limit, timeout and interval must be positive")
        deliveries = WebhookDelivery.objects.using(database)
        if options["action"] == "stats":
            counts = {
                str(row["status"]): row["count"]
                for row in deliveries.values("status").annotate(count=Count("pk"))
            }
            oldest = deliveries.filter(status="pending").aggregate(oldest=Min("event__created"))[
                "oldest"
            ]
            age = max(0.0, (timezone.now() - oldest).total_seconds()) if oldest else None
            self.stdout.write(json.dumps({"counts": counts, "oldest_pending_seconds": age}))
            return
        if options["action"] == "prune":
            days = int(str(options["days"]))
            if days <= 0:
                raise CommandError("days must be positive")
            removed = prune_events(days=days, limit=limit, using=database)
            self.stdout.write(f"removed_events={removed}")
            return
        if options["action"] == "retry":
            if options["delivery_id"] is None:
                raise CommandError("retry requires --delivery-id")
            if not retry_deliveries(deliveries.filter(pk=int(str(options["delivery_id"])))):
                raise CommandError("Delivery is missing or currently claimed by a worker")
            self.stdout.write("queued=1")
            return
        policy = URLPolicy(
            allow_http=bool(options["allow_http"]),
            allow_private_networks=bool(options["allow_private_networks"]),
        )
        while True:
            report = deliver_due(limit=limit, timeout=timeout, url_policy=policy, using=database)
            handled = report.succeeded + report.retrying + report.failed
            if handled or not options["loop"]:
                self.stdout.write(
                    f"succeeded={report.succeeded} retrying={report.retrying} "
                    f"failed={report.failed} disabled_endpoints={report.disabled_endpoints} "
                    f"lost_claims={report.lost_claims}"
                )
            if not options["loop"]:
                return
            if handled < limit:
                time.sleep(interval)

    def _encrypt_secrets(self, database: str) -> None:
        count = 0
        with transaction.atomic(using=database):
            for endpoint in (
                WebhookEndpoint.objects.using(database).select_for_update().only("pk", "secret")
            ):
                endpoint.secret = reencrypt_secret(endpoint.secret)
                endpoint.save(update_fields=["secret"])
                count += 1
        self.stdout.write(self.style.SUCCESS(f"Encrypted {count} endpoint secret(s)"))
