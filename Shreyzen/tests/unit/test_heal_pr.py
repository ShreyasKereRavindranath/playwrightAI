"""
Unit tests for the self-heal → auto-PR core (utils/heal_pr.py).

All filesystem operations run against a temp `pages/` dir — no git, no LLM, no
touching the real repo.
"""

import pytest

from utils import heal_pr as core


def _page(pages_dir, name, body):
    (pages_dir / name).write_text(body, encoding="utf-8")


@pytest.fixture
def pages_dir(tmp_path):
    d = tmp_path / "pages"
    d.mkdir()
    return d


def _entry(original, healed, intent="click the username field"):
    return {"intent": intent, "original_locator": original, "healed_locator": healed,
            "status": "PENDING_REVIEW"}


# ── find_locator_site ────────────────────────────────────────────────────────

def test_find_locator_site_single(pages_dir):
    _page(pages_dir, "login_page.py",
          'class LoginPage:\n    u = self.page.locator(\'[data-test="username"]\')\n')
    sites = core.find_locator_site('[data-test="username"]', pages_dir)
    assert len(sites) == 1 and sites[0]["count"] == 1


def test_find_locator_site_ignores_init_and_misses(pages_dir):
    _page(pages_dir, "__init__.py", '"[data-test=\\"username\\"]"\n')
    _page(pages_dir, "cart_page.py", 'x = self.page.locator("#cart")\n')
    assert core.find_locator_site('[data-test="username"]', pages_dir) == []


# ── apply_healing: the four outcomes ─────────────────────────────────────────

def test_apply_unambiguous_writes_and_diffs(pages_dir):
    _page(pages_dir, "login_page.py",
          'u = self.page.locator(\'[data-test="username"]\')\n')
    entry = _entry('[data-test="username"]', '[data-test="user-name"]')
    res = core.apply_healing(entry, pages_dir=pages_dir, root=pages_dir.parent, write=True)
    assert res.status == "applied"
    assert res.path == "pages/login_page.py"
    assert '[data-test="user-name"]' in (pages_dir / "login_page.py").read_text()
    assert "user-name" in res.diff and res.diff.startswith("---")


def test_apply_preview_does_not_write(pages_dir):
    _page(pages_dir, "login_page.py", 'u = self.page.locator("#old")\n')
    res = core.apply_healing(_entry("#old", "#new"), pages_dir=pages_dir,
                             root=pages_dir.parent, write=False)
    assert res.status == "would_apply"
    assert (pages_dir / "login_page.py").read_text() == 'u = self.page.locator("#old")\n'


def test_apply_not_found(pages_dir):
    _page(pages_dir, "login_page.py", 'u = self.page.locator("#present")\n')
    res = core.apply_healing(_entry("#missing", "#new"), pages_dir=pages_dir,
                             root=pages_dir.parent, write=True)
    assert res.status == "not_found"


def test_apply_ambiguous_when_selector_repeats(pages_dir):
    _page(pages_dir, "a_page.py", 'x = self.page.locator(".btn")\n')
    _page(pages_dir, "b_page.py", 'y = self.page.locator(".btn")\n')
    res = core.apply_healing(_entry(".btn", ".button"), pages_dir=pages_dir,
                             root=pages_dir.parent, write=True)
    assert res.status == "ambiguous"
    # Nothing was rewritten.
    assert ".button" not in (pages_dir / "a_page.py").read_text()


def test_apply_no_original(pages_dir):
    res = core.apply_healing(_entry("", "#new"), pages_dir=pages_dir,
                             root=pages_dir.parent, write=True)
    assert res.status == "no_original"


def test_apply_identical_selectors_is_noop(pages_dir):
    _page(pages_dir, "login_page.py", 'u = self.page.locator("#same")\n')
    res = core.apply_healing(_entry("#same", "#same"), pages_dir=pages_dir,
                             root=pages_dir.parent, write=True)
    assert res.status == "not_found"


# ── plan() aggregation ───────────────────────────────────────────────────────

def test_plan_aggregates_applied(pages_dir):
    _page(pages_dir, "login_page.py",
          'u = self.page.locator("#u")\np = self.page.locator("#p_old")\n')
    entries = [_entry("#u", "#u2"), _entry("#p_old", "#p2"), _entry("#gone", "#x")]
    report = core.plan(entries, pages_dir=pages_dir, root=pages_dir.parent, write=True)
    assert len(report.applied) == 2
    assert report.applied_paths == ["pages/login_page.py"]


# ── open_pr degrades gracefully outside a git repo ───────────────────────────

def test_open_pr_reports_nothing_to_commit(pages_dir):
    empty = core.ApplyReport(results=[])
    assert core.open_pr(empty, root=pages_dir.parent)["reason"] == "nothing_to_commit"


def test_open_pr_reports_not_a_git_repo(pages_dir, tmp_path):
    _page(pages_dir, "login_page.py", 'u = self.page.locator("#old")\n')
    report = core.plan([_entry("#old", "#new")], pages_dir=pages_dir,
                       root=tmp_path, write=True)
    out = core.open_pr(report, root=tmp_path)   # tmp_path has no .git
    assert out["ok"] is False and out["reason"] == "not_a_git_repo"


# ── selector extraction from a Locator repr (BasePage helper) ────────────────

def test_base_page_selector_extraction():
    from pages.base_page import BasePage

    class _FakeLocator:
        def __repr__(self):
            return "<Locator frame=<Frame name= url='http://x'> selector='[data-test=\"username\"]'>"

    assert BasePage._selector_of(_FakeLocator()) == '[data-test="username"]'


# ── CLI orchestration (status marking + exit codes), core stubbed ────────────

def test_cli_apply_marks_applied_status(monkeypatch):
    from tools import heal_pr as cli

    entry = _entry("#old", "#new")
    monkeypatch.setattr(cli.core, "load_pending", lambda *a, **k: [entry])
    report = core.ApplyReport(results=[
        core.HealResult(entry["intent"], "#old", "#new", "applied",
                        path="pages/login_page.py", diff="--- a\n+++ b\n-#old\n+#new\n")])
    monkeypatch.setattr(cli.core, "plan", lambda *a, **k: report)

    marked = []
    monkeypatch.setattr(cli.AISelfHeal, "set_status", lambda intent, status: marked.append((intent, status)))

    rc = cli.main(["--apply"])
    assert rc == 0
    assert marked == [(entry["intent"], core.APPLIED)]


def test_cli_returns_nonzero_when_nothing_applied(monkeypatch):
    from tools import heal_pr as cli
    entry = _entry("#gone", "#new")
    monkeypatch.setattr(cli.core, "load_pending", lambda *a, **k: [entry])
    report = core.ApplyReport(results=[
        core.HealResult(entry["intent"], "#gone", "#new", "not_found", detail="nope")])
    monkeypatch.setattr(cli.core, "plan", lambda *a, **k: report)
    monkeypatch.setattr(cli.AISelfHeal, "set_status", lambda *a, **k: None)

    assert cli.main([]) == 1     # candidates existed but none auto-applied
