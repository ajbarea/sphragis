"""The written guides' fetch: soft-redirect stubs followed to the page they point to."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("rules_guides", ROOT / "scripts" / "rules_guides.py")
assert spec and spec.loader
guides = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guides)


def test_a_soft_redirect_is_followed_in_either_case_and_with_text_after() -> None:
    assert guides.soft_target("{{soft redirect|wikitech:Puppet coding}}") == "Puppet coding"
    assert guides.soft_target("{{Soft redirect|wikitech:Puppet coding}}\nMoved.") == "Puppet coding"
    assert guides.soft_target("{{Soft_redirect | wikitech:Puppet coding |reason}}") == (
        "Puppet coding"
    )
    assert guides.soft_target("Conventions for PHP code.") is None


def test_a_soft_redirect_elsewhere_is_recognised_so_it_is_refused() -> None:
    assert guides.SOFT_REDIRECT.match("{{Soft redirect|meta:Page}}")
    assert guides.soft_target("{{Soft redirect|meta:Page}}") is None
