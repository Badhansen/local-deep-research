"""Tests for the check-author-identity pre-commit hook.

All test data uses throwaway addresses (``*.test`` / ``example.com``) or public
GitHub noreply addresses; no real personal email appears in this file.
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


PYPROJECT = """\
[project]
name = "x"
authors = [
    {name = "LearningCircuit", email = "185559241+LearningCircuit@users.noreply.github.com"},
    {name = "djpetti", email = "djpetti@example.com"},
]
"""
DECLARED = {
    "learningcircuit": {"185559241+learningcircuit@users.noreply.github.com"},
    "djpetti": {"djpetti@example.com"},
}


class TestParseIdentities:
    def test_parses_and_lowercases(self):
        assert mod.parse_identities(PYPROJECT) == DECLARED

    def test_key_order_independent(self):
        text = (
            'authors = [\n    {email = "bob@example.com", name = "Bob"},\n]\n'
        )
        assert mod.parse_identities(text) == {"bob": {"bob@example.com"}}

    def test_anchored_ignores_other_authors_keys(self):
        text = (
            'co_authors = ["x"]\n'
            'authors = [\n    {name = "A", email = "a@b.test"},\n]\n'
        )
        assert mod.parse_identities(text) == {"a": {"a@b.test"}}

    def test_no_block_returns_empty(self):
        assert mod.parse_identities('[project]\nname = "x"\n') == {}

    def test_multiple_emails_per_name(self):
        text = (
            "authors = [\n"
            '    {name = "A", email = "a@x.test"},\n'
            '    {name = "A", email = "a2@x.test"},\n'
            "]\n"
        )
        assert mod.parse_identities(text) == {"a": {"a@x.test", "a2@x.test"}}


class TestIsNoreply:
    def test_user_noreply_allowed(self):
        assert mod._is_noreply("123+user@users.noreply.github.com")
        assert mod._is_noreply("user@users.noreply.github.com")

    def test_personal_and_webflow_not_user_noreply(self):
        assert not mod._is_noreply("user@example.com")
        assert not mod._is_noreply("noreply@github.com")  # web-flow committer


class TestMismatch:
    def test_any_noreply_allowed_for_declared_author(self):
        # djpetti is declared with a non-noreply email; his GH noreply must pass.
        assert (
            mod._mismatch(
                "author",
                "djpetti",
                "7475340+djpetti@users.noreply.github.com",
                DECLARED,
            )
            is None
        )

    def test_declared_email_allowed(self):
        assert (
            mod._mismatch("author", "djpetti", "djpetti@example.com", DECLARED)
            is None
        )

    def test_declared_author_personal_email_flagged(self):
        msg = mod._mismatch(
            "author", "LearningCircuit", "personal@nope.test", DECLARED
        )
        assert msg is not None
        assert "LearningCircuit" in msg

    def test_case_insensitive_name_match(self):
        # A lowercase display name must still be enforced (the hashedviking gap).
        assert (
            mod._mismatch(
                "author", "learningcircuit", "personal@nope.test", DECLARED
            )
            is not None
        )

    def test_unknown_name_not_enforced(self):
        assert (
            mod._mismatch("author", "Outsider", "out@example.com", DECLARED)
            is None
        )

    def test_message_never_contains_offending_email(self):
        secret = "do-not-leak@secret.test"
        msg = mod._mismatch("author", "LearningCircuit", secret, DECLARED)
        assert msg is not None
        assert secret not in msg
        assert "secret" not in msg.lower()

    def test_empty_declared_never_flags(self):
        assert mod._mismatch("author", "Anyone", "x@y.test", {}) is None


class TestCoAuthorRegex:
    def test_matches_trailer(self):
        m = mod._CO_AUTHOR.search("b\n\nCo-authored-by: Some One <a@b.test>\n")
        assert m is not None
        assert m.group("name") == "Some One"
        assert m.group("email") == "a@b.test"

    def test_indented_trailer_matches(self):
        assert (
            mod._CO_AUTHOR.search("  Co-authored-by: X <x@y.test>") is not None
        )

    def test_no_false_match_on_prose(self):
        assert mod._CO_AUTHOR.search("This was co-authored by someone") is None


class TestParseLog:
    def test_basic_record(self):
        raw = "sha1\x00An\x00ae@x.test\x00Cn\x00ce@x.test\x00body line\x1e"
        assert mod._parse_log(raw) == [
            ("sha1", "An", "ae@x.test", "Cn", "ce@x.test", "body line")
        ]

    def test_nul_in_body_is_preserved(self):
        # A NUL earlier in the body must not truncate it: a real trailer (on its
        # own line) after the NUL must survive and still be detected.
        body = "head\x00more\n\nCo-authored-by: LearningCircuit <bad@nope.test>"
        raw = f"sha1\x00An\x00a@x.test\x00Cn\x00c@x.test\x00{body}\x1e"
        recs = mod._parse_log(raw)
        assert recs[0][5] == body  # full body kept (old fields[5] would truncate)
        errs = mod._check(recs[0], DECLARED)
        assert any("Co-authored-by" in e for e in errs)


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
        assert "deadbeef0" in errs[0]

    def test_committer_violation(self):
        errs = mod._check(
            self._rec(cn="LearningCircuit", ce="bad@nope.test"), DECLARED
        )
        assert len(errs) == 1
        assert "committer" in errs[0]

    def test_co_author_trailer_violation(self):
        errs = mod._check(
            self._rec(
                body="m\n\nCo-authored-by: LearningCircuit <bad@nope.test>\n"
            ),
            DECLARED,
        )
        assert len(errs) == 1
        assert "Co-authored-by" in errs[0]

    def test_clean_noreply_passes(self):
        good = "185559241+learningcircuit@users.noreply.github.com"
        errs = mod._check(
            self._rec(
                an="LearningCircuit", ae=good, cn="LearningCircuit", ce=good
            ),
            DECLARED,
        )
        assert errs == []

    def test_external_contributor_passes(self):
        assert (
            mod._check(self._rec(an="Outsider", ae="out@example.com"), DECLARED)
            == []
        )

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
