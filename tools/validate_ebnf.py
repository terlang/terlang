#!/usr/bin/env python3
"""Static validator for Terlang EBNF specs.

The validator is intentionally lightweight:
- checks that all referenced nonterminals are defined
- checks for strict-EBNF portability issues such as
  regex-style character classes and repetition shorthand (*, +)
- prints a concise report and exits non-zero when problems are found

Usage:
    python3 tools/validate_ebnf.py
    python3 tools/validate_ebnf.py --strict
    python3 tools/validate_ebnf.py --no-strict
    python3 tools/validate_ebnf.py path/to/file.ebnf
"""

from __future__ import annotations

import argparse
import dataclasses
import pathlib
import re
import sys
from typing import Iterable


DEFAULT_PATH = pathlib.Path("docs/grammar/TERLANG_SYNTAX_SPEC.ebnf")

# Lower-case terminals/keywords that are expected to appear in the grammar text
# but are not nonterminal references.
KEYWORDS = {
    "after",
    "and",
    "as",
    "big",
    "binary",
    "bits",
    "bitstring",
    "case",
    "catch",
    "class",
    "constructor",
    "css",
    "div",
    "do",
    "end",
    "except",
    "export",
    "extends",
    "file",
    "float",
    "for",
    "from",
    "fun",
    "general",
    "hard",
    "if",
    "impl",
    "import",
    "in",
    "integer",
    "interoperability",
    "into",
    "is",
    "little",
    "markdown",
    "match",
    "mode",
    "module",
    "native",
    "not",
    "of",
    "opaque",
    "or",
    "pattern",
    "pass",
    "pub",
    "quote",
    "receive",
    "rem",
    "reserved",
    "runtime",
    "separate",
    "server",
    "signed",
    "source",
    "struct",
    "syntax",
    "template",
    "term",
    "the",
    "their",
    "to",
    "trait",
    "try",
    "type",
    "unescape",
    "unescaped",
    "unsigned",
    "unquote",
    "v0",
    "version",
    "when",
    "with",
    "utf8",
    "utf16",
    "utf32",
}


@dataclasses.dataclass(frozen=True)
class Finding:
    line: int
    kind: str
    message: str


@dataclasses.dataclass(frozen=True)
class ValidationReport:
    path: pathlib.Path
    definitions: list[tuple[str, int]]
    undefined_symbols: list[Finding]
    strict_findings: list[Finding]

    @property
    def ok(self) -> bool:
        return not self.undefined_symbols and not self.strict_findings


def strip_comments(text: str) -> str:
    out: list[str] = []
    i = 0
    in_comment = False
    while i < len(text):
        if not in_comment and text.startswith("(*", i):
            in_comment = True
            out.append("  ")
            i += 2
            continue
        if in_comment and text.startswith("*)", i):
            in_comment = False
            out.append("  ")
            i += 2
            continue

        ch = text[i]
        if in_comment and ch not in {"\n", "\r"}:
            out.append(" ")
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def strip_quoted_and_meta(text: str) -> str:
    text = re.sub(r'"(?:\\.|[^"\\])*"', " ", text)
    text = re.sub(r"'(?:\\.|[^'\\])*'", " ", text)
    text = re.sub(r"\?(?:.|\n)*?\?", " ", text)
    return text


def find_definitions(lines: Iterable[str]) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    bare_name = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*$")
    inline = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*::=")

    line_list = list(lines)
    i = 0
    while i < len(line_list):
        line = line_list[i]
        match = inline.match(line)
        if match:
            out.append((match.group(1), i + 1))
            i += 1
            continue

        match = bare_name.match(line)
        if match:
            # Support split productions of the form:
            #   Name
            #     ::= ...
            if i + 1 < len(line_list) and line_list[i + 1].lstrip().startswith("::="):
                out.append((match.group(1), i + 1))
        i += 1

    return out


def find_references(text: str, definitions: set[str]) -> list[Finding]:
    findings: list[Finding] = []
    # Inspect each production block, which may span multiple lines.
    lines = text.splitlines()
    block_start = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*(::=)?")
    i = 0
    while i < len(lines):
        line = lines[i]
        if not block_start.match(line):
            i += 1
            continue
        block_line = i + 1

        # Only consider actual productions.
        block_lines = [line]
        if "::=" not in line:
            if i + 1 >= len(lines) or not lines[i + 1].lstrip().startswith("::="):
                i += 1
                continue
            block_lines.append(lines[i + 1])
            i += 2
        else:
            i += 1

        while i < len(lines):
            block_lines.append(lines[i])
            if lines[i].rstrip().endswith("."):
                i += 1
                break
            i += 1

        block_text = "\n".join(block_lines)
        # Remove regex-style character classes before symbol extraction so that
        # lexical productions such as [a-z] and [A-Za-z0-9_]* do not generate
        # false undefined-symbol findings.
        block_text = re.sub(r"\[[^\]]*-[^\]]*\]", " ", block_text)
        rhs = strip_quoted_and_meta(block_text)
        # Drop the rule header itself before tokenization.
        rhs = re.sub(r"^\s*[A-Za-z_][A-Za-z0-9_]*\s*::=", " ", rhs, flags=re.M)
        rhs = re.sub(r"^\s*[A-Za-z_][A-Za-z0-9_]*\s*$", " ", rhs, flags=re.M)
        candidates = set(re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", rhs))
        for name in sorted(candidates):
            if name in definitions or name in KEYWORDS:
                continue
            if name[0].islower():
                # Lower-case words can be either terminals or keywords; if they
                # are not on the keyword allowlist, they are still likely a
                # missing symbol in this grammar.
                findings.append(
                    Finding(
                        line=block_line,
                        kind="undefined-symbol",
                        message=f"reference to '{name}' is not defined",
                    )
                )
            elif name[0].isupper():
                findings.append(
                    Finding(
                        line=block_line,
                        kind="undefined-symbol",
                        message=f"reference to '{name}' is not defined",
                    )
                )
    # Deduplicate identical findings while preserving order.
    seen: set[tuple[int, str, str]] = set()
    unique: list[Finding] = []
    for finding in findings:
        key = (finding.line, finding.kind, finding.message)
        if key not in seen:
            seen.add(key)
            unique.append(finding)
    return unique


def find_strict_findings(lines: list[str]) -> list[Finding]:
    findings: list[Finding] = []
    for lineno, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line or line.startswith("(*"):
            continue
        sanitized = strip_quoted_and_meta(raw)
        if "::=" not in sanitized:
            continue
        if re.search(r"\[[A-Za-z0-9_\-]+\]", sanitized):
            findings.append(
                Finding(
                    line=lineno,
                    kind="strict-ebnf",
                    message="regex-style character class is not strict EBNF",
                )
            )
        if "*" in sanitized:
            findings.append(
                Finding(
                    line=lineno,
                    kind="strict-ebnf",
                    message="'*' repetition shorthand is not strict EBNF",
                )
            )
        if "+" in sanitized:
            findings.append(
                Finding(
                    line=lineno,
                    kind="strict-ebnf",
                    message="'+' repetition shorthand is not strict EBNF",
                )
            )
    return findings


def validate(path: pathlib.Path, strict: bool = True) -> ValidationReport:
    raw = path.read_text(encoding="utf-8")
    no_comments = strip_comments(raw)
    lines = no_comments.splitlines()
    definitions = find_definitions(lines)
    defined_names = {name for name, _ in definitions}
    undefined_symbols = find_references(no_comments, defined_names)
    strict_findings = find_strict_findings(lines) if strict else []
    return ValidationReport(path, definitions, undefined_symbols, strict_findings)


def format_report(report: ValidationReport, strict: bool) -> str:
    parts: list[str] = []
    parts.append(f"Validated {report.path}")
    parts.append(f"  definitions: {len(report.definitions)}")
    parts.append(f"  undefined symbols: {len(report.undefined_symbols)}")
    if strict:
        parts.append(f"  strict EBNF findings: {len(report.strict_findings)}")
    if report.undefined_symbols:
        parts.append("")
        parts.append("Undefined symbol findings:")
        for finding in report.undefined_symbols:
            parts.append(f"  line {finding.line}: {finding.message}")
    if report.strict_findings:
        parts.append("")
        parts.append("Strict EBNF findings:")
        for finding in report.strict_findings:
            parts.append(f"  line {finding.line}: {finding.message}")
    return "\n".join(parts)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "path",
        nargs="?",
        default=str(DEFAULT_PATH),
        help=f"EBNF file to validate (default: {DEFAULT_PATH})",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--strict",
        dest="strict",
        action="store_true",
        help="enable strict-EBNF checks (default)",
    )
    group.add_argument(
        "--no-strict",
        dest="strict",
        action="store_false",
        help="disable strict-EBNF checks and only validate symbol references",
    )
    parser.set_defaults(strict=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    path = pathlib.Path(args.path)
    if not path.exists():
        print(f"error: file not found: {path}", file=sys.stderr)
        return 2

    report = validate(path, strict=args.strict)
    print(format_report(report, strict=args.strict))

    if report.ok:
        return 0

    # Undefined symbols are always treated as failures. Strict findings fail when
    # strict mode is enabled (default behavior).
    if report.undefined_symbols or (args.strict and report.strict_findings):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
