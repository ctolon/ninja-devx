from pathlib import Path

from ninja_devx.tooling.unasync import main, unasync_source

ROOT = Path(__file__).resolve().parent.parent
PACKAGE_RULES = [
    "src/ninja_devx/_permission_eval_async.py:src/ninja_devx/_permission_eval.py",
]
PACKAGE_REPLACEMENTS = ["--replace", "adenied=denied", "--replace", "aleaf_allows=leaf_allows"]


def test_unasync_rewrites_async_syntax_and_names():
    source = """from collections.abc import AsyncIterator

class Orders:
    async def total(self, orders) -> AsyncIterator[int]:
        async with self.lock:
            count = await orders.acount()  # "await" in comments stays
        async for order in orders.aiterator():
            yield await self.price(order)
        text = "async def await"
"""
    assert (
        unasync_source(
            source, {**{"AsyncIterator": "Iterator"}, "acount": "count", "aiterator": "iterator"}
        )
        == """from collections.abc import Iterator

class Orders:
    def total(self, orders) -> Iterator[int]:
        with self.lock:
            count = orders.count()  # "await" in comments stays
        for order in orders.iterator():
            yield self.price(order)
        text = "async def await"
"""
    )


def test_cli_writes_and_checks(tmp_path, capsys):
    source = tmp_path / "aio.py"
    target = tmp_path / "sync.py"
    source.write_text("async def aget_order(repo):\n    return await repo.aget(pk=1)\n")
    rule = f"{source}:{target}"

    assert main([rule, "--check"]) == 1
    assert "outdated" in capsys.readouterr().err
    assert main([rule, "--replace", "aget_order=get_order"]) == 0
    assert target.read_text().endswith("def get_order(repo):\n    return repo.get(pk=1)\n")
    assert main([rule, "--check", "--replace", "aget_order=get_order"]) == 0


def test_package_sync_twins_are_up_to_date(monkeypatch):
    monkeypatch.chdir(ROOT)
    assert main([*PACKAGE_RULES, "--check", *PACKAGE_REPLACEMENTS]) == 0, (
        "run: python -m ninja_devx.tooling.unasync "
        + " ".join([*PACKAGE_RULES, *PACKAGE_REPLACEMENTS])
    )
