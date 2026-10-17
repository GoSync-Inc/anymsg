#!/usr/bin/env python3
"""Decide whether a pull request run changes only reviewed documentation that nothing reads (ANYM-323).

Such a run skips the test jobs; every other run, and any doubt, verifies in full. A path qualifies only when it is
named exactly in the reviewed list `.github/docs-only-paths.txt`. As a regression guard, not a proof, every other
tracked file of the tested merge commit (the tests, scripts, packaging files, Dockerfiles and workflows of every
module, and anything else) is also scanned, outside unambiguous comments, for the changed path, its file name, and
any directory or glob read of Markdown.
"""
from __future__ import annotations

import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tokenize

LIST = ".github/docs-only-paths.txt"
EXCLUSIONS = "## scan exclusions"
ROOTS = ("docs/", ".claude/")
# Shipped in packages and images, or legal texts: never docs-only, whatever the list says.
NEVER = {"README.md", "SETUP.md", "THIRD_PARTY_NOTICES.md"}
INTENT_LINE = re.compile(r"Tiden intent branch:", re.IGNORECASE)
# A directory or glob read that could pick up Markdown: a listing call, an include pattern or a Docker copy that
# names it within WINDOW lines, so a call or a list split across lines still counts.
LISTING = re.compile(r"\b(?:r?glob|iterdir|walk|scandir|listdir|find|ls|include|exclude|only-include|files|COPY|ADD)\b")
MARKDOWN = re.compile(r"\*\.md\b|(?<![\w-])docs(?![\w-])|\.claude\b")
WINDOW = 3
# Only whole-line comments are dropped, except in Python, whose tokenizer separates comments from strings; a
# comment marker inside a string or after code never hides that code.
LINE_COMMENT = {"#": (".yml", ".yaml", ".toml", ".sh", ".bash", ".cfg", ".ini", ".conf", ".dockerignore", ".gitignore"),
                "//": (".js", ".mjs", ".cjs", ".ts", ".go")}
MAX_SCANNED_BYTES = 8 * 1024 * 1024
DOCUMENT = 0o100644


def git(root: Path, *arguments: str) -> str:
    return subprocess.run(["git", "-C", str(root), *arguments], check=True, capture_output=True, text=True).stdout


def read_list(root: Path) -> tuple[set[str], dict[str, str]]:
    """The reviewed documents, and the scan exclusions with the reason each was reviewed for."""
    documents: set[str] = set()
    exclusions: dict[str, str] = {}
    section: set[str] | dict[str, str] = documents
    for raw in (root / LIST).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line == EXCLUSIONS:
            section = exclusions
        elif not line or line.startswith("#"):
            continue
        elif section is documents:
            documents.add(line)
        else:
            path, _, reason = (part.strip() for part in line.partition("|"))
            if not path or not reason:
                raise ValueError(f"scan exclusion without a reason: {line}")
            exclusions[path] = reason
    return documents, exclusions


def listable(path: str) -> bool:
    """Whether a path may be on the list at all: a Markdown file at the root, in docs/ or in .claude/."""
    parts = PurePosixPath(path).parts
    return (path.endswith(".md") and path not in NEVER and not path.upper().startswith(("LICENSE", "COPYING"))
            and ".." not in parts and (len(parts) == 1 or path.startswith(ROOTS)))


def scan_inputs(root: Path) -> list[str]:
    """Every tracked file of the tested commit that is not Markdown: the tests, scripts, packaging files,
    Dockerfiles and workflows of every module, and anything else that could read a document."""
    files = git(root, "ls-files", "-z").split("\0")
    return sorted(path for path in files if path and not path.endswith(".md") and path != LIST)


def without_python_comments(text: str) -> list[str]:
    lines = text.splitlines()
    try:
        comments = [token for token in tokenize.generate_tokens(io.StringIO(text).readline)
                    if token.type == tokenize.COMMENT]
    except (tokenize.TokenError, SyntaxError):
        return lines  # unparsable: every comment is kept, so nothing is hidden
    for token in reversed(comments):
        (row, start), (_, end) = token.start, token.end
        lines[row - 1] = lines[row - 1][:start] + lines[row - 1][end:]
    return lines


def code_lines(root: Path, path: str) -> list[str]:
    """The scanned file's lines with only unambiguous comments removed."""
    try:
        text = (root / path).read_bytes()[:MAX_SCANNED_BYTES].decode("utf-8", errors="replace")
    except OSError:
        return []
    if path.endswith(".py"):
        return without_python_comments(text)
    name = PurePosixPath(path).name
    marker = "#" if name.startswith("Dockerfile") else next(
        (marker for marker, kinds in LINE_COMMENT.items() if name.endswith(kinds)), None)
    return ["" if marker and line.lstrip().startswith(marker) else line for line in text.splitlines()]


def consumers(root: Path, changed: list[str], exclusions: dict[str, str]) -> list[str]:
    """Where scanned files name a changed document or read Markdown by directory or glob."""
    names = [re.compile(r"(?<![\w.-])" + re.escape(value) + r"(?![\w-])")
             for value in {*changed, *(PurePosixPath(path).name for path in changed)}]
    hits = []
    for path in scan_inputs(root):
        if path in exclusions:
            continue
        lines = code_lines(root, path)
        for number, line in enumerate(lines, 1):
            nearby = "\n".join(lines[max(0, number - 1 - WINDOW):number + WINDOW])
            if any(name.search(line) for name in names) or (LISTING.search(line) and MARKDOWN.search(nearby)):
                hits.append(f"{path}:{number}")
    return hits


def changes(root: Path) -> list[tuple[str, str, str, str]]:
    """(status, old mode, new mode, path) of the merge commit against its base, renames split in two."""
    raw = git(root, "diff", "-z", "--raw", "--no-renames", "--no-abbrev", "HEAD^1", "HEAD").split("\0")
    entries = []
    for header, path in zip(raw[0::2], raw[1::2]):
        old, new, _, _, status = header.lstrip(":").split(" ")
        entries.append((status, old, new, path))
    return entries


def decide(root: Path, event: str, head_sha: str, body: str) -> tuple[bool, str]:
    """(docs-only, reason). Every condition that is not established means a full verification."""
    if event != "pull_request":
        return False, f"{event or 'this'} runs always verify in full"
    if INTENT_LINE.search(body or ""):
        return False, "the pull request links a Tiden intent branch, which needs test evidence"
    parents = git(root, "rev-parse", "HEAD^@").split()
    if len(parents) != 2 or git(root, "rev-parse", "HEAD^2").strip() != head_sha:
        return False, "the pull request merge commit could not be established"
    documents, exclusions = read_list(root)
    entries = changes(root)
    if not entries:
        return False, "the pull request changes nothing"
    for status, old, new, path in entries:
        modes = {"M": (old, new), "D": (old,)}.get(status)
        if modes is None or any(int(mode, 8) != DOCUMENT for mode in modes):
            return False, f"not a plain document edit or deletion: {status} {old} {new} {path}"
        if path not in documents or not listable(path):
            return False, f"not on the reviewed docs-only list: {path}"
    hits = consumers(root, [path for _, _, _, path in entries], exclusions)
    if hits:
        return False, "scanned code may read a changed document: " + ", ".join(hits[:5])
    return True, f"only reviewed documentation changed ({len(entries)} file(s))"


def main() -> int:
    root = Path.cwd()
    event = os.environ.get("EVENT_NAME", "")
    try:
        payload = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
        body = (payload.get("pull_request") or {}).get("body") or ""
        docs_only, reason = decide(root, event, os.environ.get("HEAD_SHA", ""), body if isinstance(body, str) else "")
    except Exception as exc:  # any doubt means full verification, never a failed run
        docs_only, reason = False, f"decision failed: {type(exc).__name__}: {exc}"
    sha = git(root, "rev-parse", "HEAD").strip() if docs_only else ""
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        output.write(f"docs_only={'true' if docs_only else 'false'}\ndocs_sha={sha}\n")
    with open(os.environ.get("GITHUB_STEP_SUMMARY", os.devnull), "a") as summary:
        summary.write(f"Docs-only pull request run: {'yes' if docs_only else 'no'} ({reason})\n")
    print(f"docs_only={docs_only}: {reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
