"""A skip fails the run unless the GPU stack, or a GPU, is what is absent.

Each case runs the suite's own conftest and pyproject, laid out as in the repository, in a child
pytest, because the guard acts on the session's exit status. `sys.modules[name] = None` stands in
for an uninstalled package, so the cases read the same with the model stack installed as without.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def suite(pytester: pytest.Pytester) -> pytest.Pytester:
    (pytester.path / "tests").mkdir()
    (pytester.path / "tests" / "conftest.py").write_text(
        (_ROOT / "tests" / "conftest.py").read_text()
    )
    pytester.makepyprojecttoml((_ROOT / "pyproject.toml").read_text())
    return pytester


def _run(suite: pytest.Pytester, **modules: str) -> pytest.RunResult:
    for name, body in modules.items():
        (suite.path / "tests" / f"{name}.py").write_text(body)
    return suite.runpytest_subprocess("tests")


def _absent(module: str) -> str:
    return f'import sys\nimport pytest\nsys.modules["{module}"] = None\n'


_PASSES = "def test_y():\n    pass\n"


def test_an_absent_gpu_stack_package_is_an_accepted_skip(suite: pytest.Pytester) -> None:
    result = _run(
        suite,
        test_stack=_absent("torch") + 'torch = pytest.importorskip("torch")\n',
        # A run whose only module skipped at collection exits NO_TESTS_COLLECTED, guard or not.
        test_other=_PASSES,
    )
    result.assert_outcomes(passed=1, skipped=1)
    assert result.ret == pytest.ExitCode.OK


def test_an_absent_gpu_is_an_accepted_skip(suite: pytest.Pytester) -> None:
    body = '@pytest.mark.skipif(True, reason="needs a CUDA device")\ndef test_x():\n    pass\n'
    result = _run(suite, test_gpu="import pytest\n\n" + body)
    result.assert_outcomes(skipped=1)
    assert result.ret == pytest.ExitCode.OK


def test_an_absent_dev_package_fails_the_run(suite: pytest.Pytester) -> None:
    result = _run(
        suite,
        test_script=_absent("numpy") + 'np = pytest.importorskip("numpy")\n',
        test_other=_PASSES,
    )
    result.assert_outcomes(passed=1, skipped=1)
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    result.stdout.fnmatch_lines(
        ["*skips the suite does not accept*", "*test_script.py*could not import 'numpy'*"]
    )


@pytest.mark.parametrize(
    "body",
    [
        'def test_x():\n    pytest.skip("not here")\n',
        '@pytest.mark.skip(reason="later")\ndef test_x():\n    pass\n',
        '@pytest.mark.parametrize("n", [])\ndef test_x(n):\n    pass\n',
        "def test_x():\n    pytest.skip(\"see: could not import 'torch' here\")\n",
        'pytestmark = pytest.mark.skip(reason="module")\n\ndef test_x():\n    pass\n',
        '@pytest.fixture\ndef f():\n    pytest.skip("fixture")\n\ndef test_x(f):\n    pass\n',
    ],
    ids=[
        "skip-call",
        "skip-mark",
        "empty-parametrize",
        "module-named-mid-reason",
        "module-skip",
        "fixture-skip",
    ],
)
def test_any_other_skip_fails_the_run(suite: pytest.Pytester, body: str) -> None:
    result = _run(suite, test_x="import pytest\n\n" + body)
    result.assert_outcomes(skipped=1)
    assert result.ret == pytest.ExitCode.TESTS_FAILED


def test_an_interrupted_run_keeps_its_exit_code(suite: pytest.Pytester) -> None:
    body = (
        'def test_a():\n    pytest.skip("not here")\n\ndef test_b():\n    raise KeyboardInterrupt\n'
    )
    result = _run(suite, test_x="import pytest\n\n" + body)
    assert result.ret == pytest.ExitCode.INTERRUPTED


def test_an_xfail_is_not_a_skip(suite: pytest.Pytester) -> None:
    body = "@pytest.mark.xfail(strict=True)\ndef test_x():\n    assert False\n"
    result = _run(suite, test_x="import pytest\n\n" + body)
    result.assert_outcomes(xfailed=1)
    assert result.ret == pytest.ExitCode.OK
