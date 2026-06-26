#!/usr/bin/env python3
"""Author identity consistency check.

Ensures every commit attributed to a project author declared in
``pyproject.toml`` (the ``authors`` list) uses that author's declared email
identity, keeping contributor attribution consistent across history.

It inspects commit *metadata* (author, committer, and ``Co-authored-by``
trailers) rather than file contents, so it runs once per invocation
(``always_run: true`` / ``pass_filenames: false``):

- In CI on a pull request, it checks every commit the PR adds.
- Locally at the pre-commit stage, it checks the commit about to be made.

The allowed identities are read from ``pyproject.toml`` at runtime — nothing
is hard-coded here. A mismatching address is never printed (so it can't end
up in logs); the message only names the declared author and its expected
identity.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path


def git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True).stdout


def load_declared_identities() -> dict[str, set[str]]:
    """Map declared author name -> set of allowed (lower-cased) emails."""
    try:
        text = Path("pyproject.toml").read_text(encoding="utf-8")
    except OSError:
        return {}
    block = re.search(r"authors\s*=\s*\[(.*?)\]", text, re.S)
    identities: dict[str, set[str]] = {}
    if block:
        for name, email in re.findall(
            r'name\s*=\s*"([^"]+)"\s*,\s*email\s*=\s*"([^"]+)"', block.group(1)
        ):
            identities.setdefault(name.strip(), set()).add(
                email.strip().lower()
            )
    return identities


def _mismatch(kind: str, name: str, email: str, declared: dict[str, set[str]]):
    """Return a message if (name, email) contradicts a declared identity.

    The offending email is intentionally NOT included in the message.
    """
    name = (name or "").strip()
    email = (email or "").strip().lower()
    allowed = declared.get(name)
    if allowed and email not in allowed:
        expected = ", ".join(sorted(allowed))
        return (
            f'{kind} "{name}" is a declared author but is not using its '
            f"declared identity (expected: {expected})"
        )
    return None


_CO_AUTHOR = re.compile(
    r"^Co-authored-by:\s*(?P<name>.*?)\s*<(?P<email>[^>]+)>\s*$", re.I | re.M
)


def _check(record, declared) -> list[str]:
    sha, an, ae, cn, ce, body = record
    out = []
    for kind, name, email in (("author", an, ae), ("committer", cn, ce)):
        msg = _mismatch(kind, name, email, declared)
        if msg:
            out.append(f"  {sha[:9]}: {msg}")
    for m in _CO_AUTHOR.finditer(body or ""):
        msg = _mismatch(
            "Co-authored-by", m.group("name"), m.group("email"), declared
        )
        if msg:
            out.append(f"  {sha[:9]}: {msg}")
    return out


def _pr_range_records():
    """Records for the commits a pull request adds, or None if not in PR CI."""
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not (event_path and os.path.exists(event_path)):
        return None
    try:
        with open(event_path, encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, ValueError):
        return None
    pr = payload.get("pull_request")
    if not pr:
        return None
    base = pr.get("base", {}).get("sha")
    head = pr.get("head", {}).get("sha")
    if not (base and head):
        return None
    # CI checkouts are shallow; fetch the two endpoints (public repo -> anon ok).
    subprocess.run(
        ["git", "fetch", "--quiet", "--depth=500", "origin", head], check=False
    )
    subprocess.run(
        ["git", "fetch", "--quiet", "--depth=1", "origin", base], check=False
    )
    raw = git(
        "log",
        f"{base}..{head}",
        "--format=%H%x00%an%x00%ae%x00%cn%x00%ce%x00%B%x1e",
    )
    if not raw.strip():
        # Could not determine the range (e.g. fetch hiccup). Don't hard-fail CI
        # on infrastructure; the local hook and a re-run still cover it.
        print(
            "author-identity: could not resolve the PR commit range; skipping "
            "(non-fatal).",
            file=sys.stderr,
        )
        return []
    records = []
    for chunk in raw.split("\x1e"):
        chunk = chunk.strip("\n")
        if not chunk:
            continue
        fields = chunk.split("\x00")
        if len(fields) >= 6:
            records.append(
                (
                    fields[0],
                    fields[1],
                    fields[2],
                    fields[3],
                    fields[4],
                    fields[5],
                )
            )
    return records


def _pending_record():
    """The identity of the commit about to be created (local pre-commit stage)."""

    def parse(ident: str):
        m = re.match(r"^(.*)<([^>]+)>", ident)
        return (m.group(1).strip(), m.group(2).strip()) if m else ("", "")

    an, ae = parse(git("var", "GIT_AUTHOR_IDENT"))
    cn, ce = parse(git("var", "GIT_COMMITTER_IDENT"))
    return [("pending00", an, ae, cn, ce, "")]


def main() -> int:
    declared = load_declared_identities()
    if not declared:
        return 0
    records = _pr_range_records()
    if records is None:
        records = _pending_record()
    errors = []
    for rec in records:
        errors.extend(_check(rec, declared))
    if errors:
        print("Author identity check failed:\n", file=sys.stderr)
        print("\n".join(errors), file=sys.stderr)
        print(
            "\nA commit is attributed to a declared author but uses a different "
            "email.\nRe-author it with its declared identity, for example:\n"
            "  git commit --amend --reset-author        # latest commit\n"
            "and make sure your git user.email matches the declared identity.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
