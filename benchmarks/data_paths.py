"""Measure related-data, visibility, grants, validation and export costs in a private DB.

    uv run python benchmarks/data_paths.py --rows 10000 --samples 20 --output /tmp/data.json

Cold means the first request in this process, not a flushed OS/database cache. RSS is
process peak RSS on Linux, not per-request allocation. Results are observations, not gates.
"""

import argparse
import json
import math
import os
import resource
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Annotated


def measure(call, samples):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    timings, queries, sizes = [], [], []
    for _ in range(samples + 1):
        with CaptureQueriesContext(connection) as captured:
            start = time.perf_counter()
            response = call()
            content = response.content  # consume exports inside the measured query interval
            elapsed = (time.perf_counter() - start) * 1000
        assert response.status_code in (200, 201, 422), response.content
        timings.append(elapsed)
        queries.append(len(captured))
        sizes.append(len(content))
    warm = sorted(timings[1:])
    return {
        "cold_ms": round(timings[0], 3),
        "warm_ms": {
            str(p): round(warm[min(len(warm) - 1, math.ceil(len(warm) * p / 100) - 1)], 3)
            for p in (50, 95, 99)
        },
        "queries_cold": queries[0],
        "queries_warm_median": statistics.median(queries[1:]),
        "response_bytes": sizes[-1],
        "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    }


def run(rows, samples):
    import django

    django.setup()
    from django.contrib.auth.models import Permission, User
    from django.contrib.contenttypes.models import ContentType
    from django.core.management import call_command
    from django.db import connections
    from ninja import Schema
    from ninja.testing import TestClient

    from ninja_devx import IsStaff
    from ninja_devx.contrib.grants.models import ObjectGrant
    from ninja_devx.crud import BulkCreateMixin, CRUDController, ReadOnlyModelController
    from ninja_devx.crud.transfer import ExportMixin
    from ninja_devx.security.object_permissions import ObjectPermissions
    from ninja_devx.serialization.visibility import VisibleTo
    from tests.testapp.models import Article, Comment, Tag

    call_command("migrate", verbosity=0, run_syncdb=True)
    user = User.objects.create(username="benchmark")
    articles = Article.objects.bulk_create(
        [
            Article(title=f"Article {i}", slug=f"article-{i}", author=user, body="private")
            for i in range(rows)
        ]
    )
    Comment.objects.bulk_create(
        [Comment(article=article, body=f"comment {i}") for article in articles for i in range(3)]
    )
    content_type = ContentType.objects.get_for_model(Article)
    permission = Permission.objects.get(content_type=content_type, codename="view_article")
    ObjectGrant.objects.bulk_create(
        [
            ObjectGrant(
                content_type=content_type, permission=permission, object_pk=str(a.pk), user=user
            )
            for a in articles
        ]
    )

    class CommentOut(Schema):
        body: str

    class ArticleOut(Schema):
        id: int
        title: str
        author_name: str
        comments: list[CommentOut]
        body: Annotated[str | None, VisibleTo(IsStaff())] = None

        @staticmethod
        def resolve_author_name(obj):
            return obj.author.username

    class Articles(ExportMixin[Article, ArticleOut], ReadOnlyModelController[Article, ArticleOut]):
        object_permissions = ObjectPermissions()

        def get_queryset(self, request):
            # Arbitrary resolver dependencies require an explicit loading hint.
            return Article.objects.select_related("author")

    class TagIn(Schema):
        name: str

    class TagOut(TagIn):
        id: int

    class Tags(BulkCreateMixin[Tag, TagOut, TagIn], CRUDController[Tag, TagOut, TagIn]):
        pass

    client = TestClient(Articles.as_router())
    bulk = TestClient(Tags.as_router())
    result = {
        "rows": rows,
        "related_rows": rows * 3,
        "warm_samples": samples,
        "backend": "private SQLite",
        "python": sys.version.split()[0],
        "django": django.get_version(),
        "related_visible_grants_list": measure(lambda: client.get("/", user=user), samples),
        "related_visible_grants_export": measure(
            lambda: client.get("/export?format=jsonl", user=user), samples
        ),
        "bulk_duplicate_validation_rollback": measure(
            lambda: bulk.post("/bulk", json=[{"name": "same"}, {"name": "same"}]), samples
        ),
    }
    assert Tag.objects.count() == 0
    connections.close_all()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=10000)
    parser.add_argument("--samples", type=int, default=20)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.rows < 1 or args.samples < 2:
        parser.error("rows must be positive and samples must be at least 2")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    with tempfile.TemporaryDirectory(prefix="devx-data-bench-") as directory:
        os.environ["DJANGO_SETTINGS_MODULE"] = "benchmarks.settings"
        os.environ["BENCH_SQLITE"] = str(Path(directory) / "data.sqlite3")
        os.environ.pop("BENCH_DATABASE_URL", None)
        result = run(args.rows, args.samples)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
