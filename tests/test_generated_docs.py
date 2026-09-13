"""The configuration reference (docs/options) is generated from the code and must be current."""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_configuration_reference_is_up_to_date():
    spec = importlib.util.spec_from_file_location(
        "generate_docs", ROOT / "tools" / "generate_docs.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["generate_docs"] = module  # dataclasses look their module up
    spec.loader.exec_module(module)
    stale = [
        str(path.relative_to(ROOT))
        for path, content in module.render_all().items()
        if not path.exists() or path.read_text() != content
    ]
    assert not stale, f"run `uv run python tools/generate_docs.py`; outdated: {stale}"


def test_every_public_option_is_described():
    for page in (ROOT / "docs" / "options").glob("*.md"):
        for line in page.read_text().splitlines():
            if line.startswith("| `") and line.rstrip().endswith("|  |"):
                raise AssertionError(f"{page.name}: undocumented row {line}")
