from __future__ import annotations

from datetime import timedelta
from typing import cast

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.utils import timezone

from ninja_devx.contrib.apikeys.auth import create_api_key, revoke_api_key
from ninja_devx.contrib.apikeys.models import APIKey


class Command(BaseCommand):
    help = "Create or revoke scoped API keys."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("action", choices=["create", "revoke"], help="what to do")
        parser.add_argument("--user", help="username of the key owner (create)")
        parser.add_argument("--name", help="label of the key (create)")
        parser.add_argument(
            "--scope", action="append", default=[], help="granted scope, repeatable"
        )
        parser.add_argument("--days", type=int, help="expire after this many days")
        parser.add_argument("--rate", default="", help='rate limit of the key, e.g. "1000/hour"')
        parser.add_argument("--prefix", help="prefix of the key to revoke")

    def handle(self, *args: object, **options: object) -> None:
        if options["action"] == "revoke":
            key = APIKey.objects.filter(prefix=str(options["prefix"])).first()
            if key is None:
                raise CommandError("No key with that prefix")
            revoke_api_key(key)
            self.stdout.write(self.style.SUCCESS(f"Revoked {key}"))
            return
        username = options["user"]
        if not username or not options["name"]:
            raise CommandError("create needs --user and --name")
        user_model = get_user_model()
        try:
            field: str = getattr(user_model, "USERNAME_FIELD", "username")
            lookup = {field: str(username)}
            user = user_model._default_manager.get(**lookup)
        except user_model.DoesNotExist as exc:
            raise CommandError(f"Unknown user {username!r}") from exc
        days = options["days"]
        expires = timezone.now() + timedelta(days=days) if isinstance(days, int) else None
        given: object = options["scope"]
        items: list[object] = cast("list[object]", given) if isinstance(given, list) else []
        scopes = [str(scope) for scope in items]
        key, raw = create_api_key(
            user,
            str(options["name"]),
            scopes=scopes,
            expires_at=expires,
            rate_limit=str(options["rate"]),
        )
        self.stdout.write(f"Created {key}; the key is shown once:\n{raw}")
