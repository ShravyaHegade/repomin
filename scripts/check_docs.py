#!/usr/bin/env python3
"""Check Markdown files for portable encoding and balanced code fences.

The checker intentionally uses only the Python standard library.  It is a
read-only check: files are opened as bytes so a CRLF conversion or an invalid
UTF-8 sequence cannot be hidden by text-mode newline translation.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import re
import sys
from typing import Iterable, List, Optional, Sequence, Tuple


ROOT = Path(__file__).resolve().parents[1]

# These directories are either generated or contain third-party sources.  In
# particular, scanning a virtual environment would make a contributor check
# depend on files outside the repository's documentation.
_SKIP_DIRECTORIES = frozenset(
    {
        ".git",
        ".hg",
        ".mypy_cache",
        ".pytest_cache",
        ".tox",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
        "venv",
    }
)
_OPENING_FENCE = re.compile(r"^[ ]{0,3}(`{3,}|~{3,})(.*)$")
_CLOSING_FENCE = re.compile(r"^[ ]{0,3}(`{3,}|~{3,})[ \t]*$")


@dataclass(frozen=True)
class DocumentationIssue:
    """One actionable documentation quality finding."""

    path: Path
    rule: str
    message: str
    line: Optional[int] = None


def find_markdown_files(root: Path) -> Tuple[Path, ...]:
    """Return Markdown files below *root* in deterministic path order.

    ``os.walk`` is used instead of a shell or Git command so the check also
    works in source archives and in temporary directories used by tests.
    """

    root = Path(root)
    if root.is_file():
        return (root,) if root.suffix.lower() == ".md" else ()
    if not root.is_dir():
        return ()

    files: List[Path] = []
    for directory, names, filenames in os.walk(root, followlinks=False):
        names[:] = sorted(
            name for name in names if name not in _SKIP_DIRECTORIES
        )
        for filename in sorted(filenames):
            if Path(filename).suffix.lower() == ".md":
                candidate = Path(directory) / filename
                # Do not follow a symlink to a document outside the checkout.
                if not candidate.is_symlink():
                    files.append(candidate)
    files.sort(key=lambda path: path.relative_to(root).as_posix())
    return tuple(files)


def _line_number_at(data: bytes, offset: int) -> int:
    """Return a one-based line number for a byte offset."""

    return data[:offset].count(b"\n") + 1


def _line_ending_issue(path: Path, data: bytes) -> Optional[DocumentationIssue]:
    """Find CRLF or bare CR bytes and describe their first line."""

    crlf_count = data.count(b"\r\n")
    bare_cr_count = data.replace(b"\r\n", b"").count(b"\r")
    if not crlf_count and not bare_cr_count:
        return None

    first_cr = data.find(b"\r")
    details: List[str] = []
    if crlf_count:
        details.append("%d CRLF" % crlf_count)
    if bare_cr_count:
        details.append("%d bare CR" % bare_cr_count)
    return DocumentationIssue(
        path,
        "line-endings",
        "use LF line endings (found %s)" % ", ".join(details),
        _line_number_at(data, first_cr),
    )


def _fence_issues(path: Path, text: str) -> List[DocumentationIssue]:
    """Return findings for Markdown fenced blocks that do not close."""

    issues: List[DocumentationIssue] = []
    opening: Optional[Tuple[str, int, int]] = None
    for line_number, line in enumerate(text.splitlines(), 1):
        if opening is None:
            match = _OPENING_FENCE.match(line)
            if match is not None:
                marker = match.group(1)
                opening = (marker[0], len(marker), line_number)
            continue

        match = _CLOSING_FENCE.match(line)
        if match is None:
            continue
        marker = match.group(1)
        if marker[0] == opening[0] and len(marker) >= opening[1]:
            opening = None

    if opening is not None:
        character, length, line_number = opening
        marker = character * length
        issues.append(
            DocumentationIssue(
                path,
                "markdown-fence",
                "unclosed fenced code block opened with %s" % marker,
                line_number,
            )
        )
    return issues


def check_markdown_file(path: Path) -> List[DocumentationIssue]:
    """Check one Markdown file without modifying it."""

    path = Path(path)
    try:
        data = path.read_bytes()
    except OSError as exc:
        return [
            DocumentationIssue(path, "read", "could not read file: %s" % exc)
        ]

    issues: List[DocumentationIssue] = []
    line_ending_issue = _line_ending_issue(path, data)
    if line_ending_issue is not None:
        issues.append(line_ending_issue)

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        issues.append(
            DocumentationIssue(
                path,
                "utf8",
                "file is not valid UTF-8 (%s)" % exc.reason,
                _line_number_at(data, exc.start),
            )
        )
        return issues

    issues.extend(_fence_issues(path, text))
    return issues


def check_documents(paths: Iterable[Path]) -> List[DocumentationIssue]:
    """Check each path and return findings in stable order."""

    issues: List[DocumentationIssue] = []
    for path in sorted((Path(path) for path in paths), key=lambda p: str(p)):
        issues.extend(check_markdown_file(path))
    return issues


def check_tree(root: Path = ROOT) -> List[DocumentationIssue]:
    """Check all Markdown files below *root*."""

    return check_documents(find_markdown_files(Path(root)))


def _display_path(path: Path, root: Path) -> str:
    """Format a path relative to root when possible."""

    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


def _format_issue(issue: DocumentationIssue, root: Path) -> str:
    location = _display_path(issue.path, root)
    if issue.line is not None:
        location += ":%d" % issue.line
    return "%s: %s: %s" % (location, issue.rule, issue.message)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check repository Markdown encoding and code fences."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="repository root to scan (default: %(default)s)",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="optional Markdown files or directories relative to --root",
    )
    return parser


def _selected_files(root: Path, paths: Sequence[Path]) -> Tuple[Path, ...]:
    if not paths:
        return find_markdown_files(root)

    selected: List[Path] = []
    for supplied in paths:
        path = supplied if supplied.is_absolute() else root / supplied
        if path.is_dir():
            selected.extend(find_markdown_files(path))
        elif path.suffix.lower() == ".md":
            selected.append(path)
    # Preserve deterministic output when a directory and one of its children
    # are supplied together.
    return tuple(sorted(set(selected), key=lambda path: str(path)))


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    root = args.root.resolve()
    files = _selected_files(root, args.paths)
    issues = check_documents(files)
    if issues:
        for issue in issues:
            print(_format_issue(issue, root), file=sys.stderr)
        print(
            "Documentation check failed: %d issue(s) in %d Markdown file(s)."
            % (len(issues), len(files)),
            file=sys.stderr,
        )
        return 1

    print("Documentation check passed (%d Markdown file(s))." % len(files))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
