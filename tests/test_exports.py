import subprocess
import sys

import ninja_devx
from ninja_devx import crud


def test_lazy_exports_match_all():
    assert set(ninja_devx._EXPORTS) == set(ninja_devx.__all__)
    for name in ninja_devx.__all__:
        assert getattr(ninja_devx, name) is not None
    assert dir(ninja_devx) == sorted(ninja_devx.__all__)


def test_unknown_attribute():
    import pytest

    with pytest.raises(AttributeError, match="has no attribute 'nope'"):
        ninja_devx.nope  # noqa: B018


def test_crud_exports_resolve():
    for name in crud.__all__:
        assert getattr(crud, name) is not None


def test_importing_the_package_does_not_need_django_settings():
    code = "import ninja_devx, sys; assert 'ninja' not in sys.modules; print(ninja_devx.__all__[0])"
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
