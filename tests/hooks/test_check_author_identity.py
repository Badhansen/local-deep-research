"""Tests for the check-author-identity pre-commit hook.

All test data uses throwaway addresses (``*.test`` / ``example.com``); no real
contributor email appears in this file.
"""

import importlib.util
from pathlib import Path

# Load the hook module by path (hyphenated filename isn't importable directly).
_HOOK_PATH = (
    Path(__file__).resolve().parents[2]
    / ".pre-commit-hooks"
    / "check-author-identity.py"
)
_spec = importlib.util.spec_from_file_location(
    "check_author_identity", _HOOK_PATH
)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


# Declared identities used across tests (mirrors the pyproject shape).
DECLARED = {
    "LearningCircuit": {"185559241+learningcircuit@users.noreply.github.com"},
    "djpetti": {"djpetti@example.com"},
}


class TestLoadDeclaredIdentities:
    def test_parses_authors_block(self, tmp_path, monkeypatch):
        (tmp_path / "pyproject.toml").write_text(
            "authors = [\n"
            '    {name = "Alice", email = "1+Alice@users.noreply.github.com"},\n'
            '    {name = "Bob", email = "bob@example.com"},\n'
            "]\n"
        )
        monkeypatch.chdir(tmp_path)
        assert mod.load_declared_identities() == {
            "Alice": {"1+alice@users.noreply.github.com"},  # lower-cased
            "Bob": {"bob@example.com"},
        }

    def test_missing_pyproject_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert mod.load_declared_identities() == {}

    def test_no_authors_block_returns_empty(self, tmp_path, monkeypatch):
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n')
        monkeypatch.chdir(tmp_path)
        assert mod.load_declared_identities() == {}


class TestMismatch:
    def test_declared_name_declared_email_ok_case_insensitive(self):
        assert (
            mod._mismatch(
                "author",
                "LearningCircuit",
                "185559241+LearningCircuit@users.noreply.github.com",
                DECLARED,
            )
            is None
        )

    def test_declared_name_foreign_email_flagged(self):
        msg = mod._mismatch(
            "author", "LearningCircuit", "foreign@nope.test", DECLARED
        )
        assert msg is not None
        assert "LearningCircuit" in msg

    def test_violation_message_never_contains_offending_email(self):
        secret = "do-not-leak@secret.test"
        msg = mod._mismatch("author", "LearningCircuit", secret, DECLARED)
        assert msg is not None
        assert secret not in msg
        assert "secret" not in msg.lower()

    def test_unknown_name_not_flagged(self):
        assert (
            mod._mismatch("author", "RandomDev", "rd@example.com", DECLARED)
            is None
        )

    def test_declared_author_with_own_email_ok(self):
        assert (
            mod._mismatch("author", "djpetti", "djpetti@example.com", DECLARED)
            is None
        )

    def test_whitespace_and_case_tolerant(self):
        assert (
            mod._mismatch(
                "author",
                "  LearningCircuit  ",
                "  185559241+LEARNINGCIRCUIT@users.noreply.github.com  ",
                DECLARED,
            )
            is None
        )

    def test_empty_declared_never_flags(self):
        assert mod._mismatch("author", "Anyone", "any@x.test", {}) is None


class TestCoAuthorRegex:
    def test_matches_trailer(self):
        m = mod._CO_AUTHOR.search(
            "body\n\nCo-authored-by: Some One <a@b.test>\n"
        )
        assert m is not None
        assert m.group("name") == "Some One"
        assert m.group("email") == "a@b.test"

    def test_case_insensitive(self):
        assert mod._CO_AUTHOR.search("co-authored-by: X <x@y.test>") is not None

    def test_no_false_match_on_prose(self):
        assert mod._CO_AUTHOR.search("This was co-authored by someone") is None


class TestCheck:
    @staticmethod
    def _rec(an="A", ae="a@x.test", cn="C", ce="c@x.test", body=""):
        return ("deadbeef0123", an, ae, cn, ce, body)

    def test_author_violation(self):
        errs = mod._check(
            self._rec(an="LearningCircuit", ae="bad@nope.test"), DECLARED
        )
        assert len(errs) == 1
        assert "author" in errs[0]
        assert "deadbeef0" in errs[0]  # short sha included

    def test_committer_violation(self):
        errs = mod._check(
            self._rec(cn="LearningCircuit", ce="bad@nope.test"), DECLARED
        )
        assert len(errs) == 1
        assert "committer" in errs[0]

    def test_co_author_trailer_violation(self):
        errs = mod._check(
            self._rec(
                body="msg\n\nCo-authored-by: LearningCircuit <bad@nope.test>\n"
            ),
            DECLARED,
        )
        assert len(errs) == 1
        assert "Co-authored-by" in errs[0]

    def test_clean_record_passes(self):
        good = "185559241+learningcircuit@users.noreply.github.com"
        errs = mod._check(
            self._rec(
                an="LearningCircuit", ae=good, cn="LearningCircuit", ce=good
            ),
            DECLARED,
        )
        assert errs == []

    def test_external_contributor_passes(self):
        errs = mod._check(
            self._rec(an="Outsider", ae="out@example.com"), DECLARED
        )
        assert errs == []

    def test_no_offending_email_in_any_message(self):
        secret = "leak-me@secret.test"
        errs = mod._check(
            self._rec(
                an="LearningCircuit",
                ae=secret,
                body=f"x\n\nCo-authored-by: LearningCircuit <{secret}>\n",
            ),
            DECLARED,
        )
        assert errs
        assert all(secret not in e for e in errs)
