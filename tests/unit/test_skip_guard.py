"""A skip fails the run unless the GPU stack, or a GPU, is what is absent.

Each case runs the suite's own conftest and pyproject in a child pytest, because the guard acts
on the session's exit status. `sys.modules[name] = None` stands in for an uninstalled package,
so the cases read the same on a machine with the model stack as on one without.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def suite(pytester: pytest.Pytester) -> pytest.Pytester:
    pytester.makeconftest((_ROOT / "tests" / "conftest.py").read_text())
    pytester.makepyprojecttoml((_ROOT / "pyproject.toml").read_text())
    return pytester


def _absent(module: str) -> str:
    return f'import sys\nimport pytest\nsys.modules["{module}"] = None\n'


def test_an_absent_gpu_stack_package_is_an_accepted_skip(suite: pytest.Pytester) -> None:
    suite.makepyfile(
        test_stack=_absent("torch")
        + 'torch = pytest.importorskip("torch")\n\ndef test_x():\n    pass\n',
        # A run whose only module skipped at collection exits NO_TESTS_COLLECTED, guard or not.
        test_other="def test_y():\n    pass\n",
    )
    result = suite.runpytest_subprocess()
    result.assert_outcomes(passed=1, skipped=1)
    assert result.ret == pytest.ExitCode.OK


def test_an_absent_gpu_is_an_accepted_skip(suite: pytest.Pytester) -> None:
    path = suite.makepyfile(
        'import pytest\n\n@pytest.mark.skipif(True, reason="needs a CUDA device")\n'
        "def test_x():\n    pass\n"
    )
    result = suite.runpytest_subprocess(path)
    result.assert_outcomes(skipped=1)
    assert result.ret == pytest.ExitCode.OK


def test_an_absent_dev_package_fails_the_run(suite: pytest.Pytester) -> None:
    path = suite.makepyfile(
        _absent("numpy") + 'np = pytest.importorskip("numpy")\n\ndef test_x():\n    pass\n'
    )
    result = suite.runpytest_subprocess(path)
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    result.stdout.fnmatch_lines(
        ["*other than the GPU stack being absent*could not import 'numpy'*"]
    )


@pytest.mark.parametrize(
    "body",
    [
        'def test_x():\n    pytest.skip("not here")\n',
        '@pytest.mark.skip(reason="later")\ndef test_x():\n    pass\n',
        '@pytest.mark.parametrize("n", [])\ndef test_x(n):\n    pass\n',
    ],
    ids=["skip-call", "skip-mark", "empty-parametrize"],
)
def test_any_other_skip_fails_the_run(suite: pytest.Pytester, body: str) -> None:
    path = suite.makepyfile("import pytest\n\n" + body)
    result = suite.runpytest_subprocess(path)
    result.assert_outcomes(skipped=1)
    assert result.ret == pytest.ExitCode.TESTS_FAILED


def test_an_xfail_is_not_a_skip(suite: pytest.Pytester) -> None:
    path = suite.makepyfile(
        "import pytest\n\n@pytest.mark.xfail(strict=True)\ndef test_x():\n    assert False\n"
    )
    result = suite.runpytest_subprocess(path)
    result.assert_outcomes(xfailed=1)
    assert result.ret == pytest.ExitCode.OK
