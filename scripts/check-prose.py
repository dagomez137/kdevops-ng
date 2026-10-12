#!/usr/bin/env python3
# SPDX-License-Identifier: copyleft-next-0.3.1
"""Refuse the phrasing top kernel maintainers almost never write.

    check-prose.py --message FILE|-    # a commit message, as the hook sees it
    check-prose.py --commit REV        # REV's message and the prose it adds
    check-prose.py --file PATH...      # all the prose in each file

A commit's prose is its message, the lines it adds to .rst and .md files, and
the comment and docstring lines it adds to .py, .sh and .nix files; vendor/
keeps its own conventions. DENY fails, WARN only prints. The patterns and the
rates that justify them are in docs/contributing/writing.rst.
"""

import argparse
import ast
import re
import subprocess
import sys

DENY = {
    "AI vocabulary": r"\b(?:delv(?:e|es|ed|ing)|leverag(?:e|es|ed|ing)|pivotal"
    r"|meticulous(?:ly)?|intricat(?:e|ely|ies)|showcas(?:e|es|ed|ing)"
    r"|foster(?:s|ed|ing)?|tapestry|holistic(?:ally)?"
    r"|empower(?:s|ed|ing|ment)?|bolster(?:s|ed|ing)?|vital(?:ly)?"
    r"|cutting-edge|state-of-the-art)\b",
    "plays a role": r"\bplay(?:s|ed|ing)? an? (?:\w+ )?role\b",
    "filler opener": r"(?:^|[.!?]\s+)(?:Importantly|In summary|In conclusion"
    r"|Previously),",
    "'This patch' opener": r"\bThis (?:commit|patch|change|series|PR) (?:adds"
    r"|introduces|implements|updates|refactors|improves|ensures|fixes|removes"
    r"|enhances|provides|addresses|makes|changes|moves)\b",
    "en or em dash": r"[–—]",
    "headed section": r"^\s*(?:Key changes|Changes made|Summary of changes"
    r"|Benefits|Root cause|Impact)\s*:\s*$",
}
WARN = {
    "inflated word": r"\b(?:enhanc(?:e|es|ed|ing|ement|ements)"
    r"|facilitat(?:e|es|ed|ing)|comprehensive(?:ly)?|seamless(?:ly)?"
    r"|crucial(?:ly)?)\b",
    "connective opener": r"(?:^|[.!?]\s+)(?:Additionally|Furthermore|Moreover"
    r"|Overall),",
    "worth noting": r"\b[Ii]t(?: is|'s) worth (?:noting|mentioning|pointing out)\b",
    "ensuring tail": r", ensuring\b|\bwhich ensures\b",
    "in order to": r"\bin order to\b",
    "abstract quality": r"\b(?:readability|maintainability)\b",
    "key + noun": r"\bkey\s+(?:aspect|benefit|feature|role|component|factor"
    r"|improvement|insight|consideration|challenge)s?\b",
    "variety of": r"\ba (?:wide )?variety of\b",
}
# Tested on the first line of a comment block only.
COMMENT_DENY = {
    "history in a comment": r"^(?:Added|Fixed|Changed|Updated|Removed"
    r"|Refactored|New:)\b",
    "restates the code": r"^(?:Increment|Decrement)s?\b",
}
COMMENT_WARN = {
    "restates the code": r"^(?:Check (?:if|whether)|(?:Loop|Iterate)s? "
    r"(?:over|through))\b",
    "'This function' opener": r"^(?:This|The) (?:function|method|helper|script"
    r"|module|class|step) (?:is used to|is responsible for|will|handles"
    r"|takes care of)\b",
    "helper opener": r"^(?:A )?[Hh]elper (?:function )?(?:to|for|that)\b",
}
TRAILER = re.compile(r"^[A-Za-z][A-Za-z-]*: \S")
COMMENTED = {".py": "#", ".sh": "#", ".nix": "#"}


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, check=True, text=True
    ).stdout


def message_lines(msg: str) -> list[tuple[int, str]]:
    """The prose lines of a message: no trailer block, quoted log or code."""
    lines = msg.rstrip("\n").splitlines()
    end = len(lines)
    while end and (TRAILER.match(lines[end - 1]) or not lines[end - 1].strip()):
        end -= 1
    return [
        (n, line)
        for n, line in enumerate(lines[:end], 1)
        if not line.startswith(("\t", "    ", ">"))
    ]


def scan(where: str, lines, deny: dict, warn: dict) -> int:
    bad = 0
    for n, text in lines:
        for name, pattern in deny.items():
            if re.search(pattern, text):
                print(f"error: {where}:{n}: {name}: {text.strip()}")
                bad = 1
        for name, pattern in warn.items():
            if re.search(pattern, text):
                print(f"warning: {where}:{n}: {name}: {text.strip()}")
    return bad


def check_message(where: str, msg: str) -> int:
    lines = message_lines(msg)
    bad = scan(where, lines, DENY, WARN)
    listed = [n for n, text in lines[1:] if re.match(r"\s*(?:[-*]|\d+[.)])\s", text)]
    if len(listed) > 1:
        print(f"error: {where}:{listed[0]}: a list in the body; write paragraphs")
        bad = 1
    return bad


def added_lines(rev: str) -> dict[str, list[tuple[int, str]]]:
    """The lines rev adds, by file, with their new line numbers."""
    diff = git("show", "--format=", "--unified=0", "--no-renames", rev)
    added: dict[str, list[tuple[int, str]]] = {}
    path, n = None, 0
    for line in diff.splitlines():
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else None
        elif line.startswith("@@"):
            n = int(re.search(r"\+(\d+)", line).group(1))  # type: ignore[union-attr]
        elif line.startswith("+") and path:
            added.setdefault(path, []).append((n, line[1:]))
            n += 1
    return added


def docstring_lines(source: str) -> set[int]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    spans = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                spans.update(range(body[0].lineno, (body[0].end_lineno or 0) + 1))
    return spans


def check_prose(path: str, lines: list[tuple[int, str]], source: str) -> int:
    """Check the prose among lines of path, whose full text is source."""
    suffix = path[path.rfind(".") :] if "." in path else ""
    if suffix in (".rst", ".md"):
        return scan(path, lines, DENY, WARN)
    if suffix not in COMMENTED:
        return 0
    docs = docstring_lines(source) if suffix == ".py" else set()
    prose, first, previous = [], [], 0
    for n, text in lines:
        stripped = text.strip()
        if stripped.startswith("#") and not stripped.startswith("#!"):
            comment = stripped.lstrip("#").strip()
            prose.append((n, comment))
            if previous != n - 1:
                first.append((n, comment))
            previous = n
        elif n in docs:
            prose.append((n, text))
    return scan(path, prose, DENY, WARN) | scan(path, first, COMMENT_DENY, COMMENT_WARN)


def check_commit(rev: str) -> int:
    bad = check_message(f"{rev[:12]} message", git("log", "-1", "--format=%B", rev))
    for path, lines in added_lines(rev).items():
        if not path.startswith("vendor/"):
            bad |= check_prose(path, lines, git("show", f"{rev}:{path}"))
    return bad


def check_file(path: str) -> int:
    with open(path) as fh:
        source = fh.read()
    return check_prose(path, list(enumerate(source.splitlines(), 1)), source)


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--message", metavar="FILE")
    group.add_argument("--commit", metavar="REV")
    group.add_argument("--file", metavar="PATH", nargs="+")
    args = parser.parse_args()
    if args.message:
        fh = sys.stdin if args.message == "-" else open(args.message)
        with fh:
            return check_message("commit message", fh.read())
    if args.file:
        return max(check_file(path) for path in args.file)
    return check_commit(git("rev-parse", args.commit).strip())


if __name__ == "__main__":
    sys.exit(main())
