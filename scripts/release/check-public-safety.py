#!/usr/bin/env python3
from __future__ import annotations

import argparse
import dataclasses
import os
import re
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path


@dataclasses.dataclass(frozen=True, slots=True)
class Pattern:
    label: str
    regex: re.Pattern[str]


@dataclasses.dataclass(frozen=True, slots=True)
class Finding:
    path: Path
    line_number: int
    label: str
    match: str

    def format(self) -> str:
        return f"{self.path.as_posix()}:{self.line_number}: {self.label}: {self.match}"


CONFIGURED_TERMS_ENV = "DORY_PUBLIC_SAFETY_PRIVATE_TERMS"

BASE_TEXT_PATTERNS = [
    Pattern("private network address", re.compile(r"\b192\.168\.\d{1,3}\.\d{1,3}\b")),
    Pattern(
        "private local path",
        re.compile(
            r"(?:"
            r"/(?:Users|Volumes|private/var/folders)/[^/\s\"'<>]+(?:/[^/\s\"'<>]+)*"
            r"|[A-Za-z]:\\Users\\[^\\\s\"'<>]+(?:\\[^\\\s\"'<>]+)*"
            r")"
        ),
    ),
]

SECRET_ASSIGNMENT = re.compile(
    r"\b"
    r"(?P<key>(?:[A-Z][A-Z0-9_]*(?:API_)?(?:KEY|TOKEN|SECRET)|"
    r"(?:OPENAI|ANTHROPIC|OPENROUTER|GOOGLE|GEMINI|GITHUB|AWS|AZURE|SLACK|NOTION|LINEAR|SUPABASE|FIREBASE|CLOUDFLARE|HUGGING_FACE|HF)(?:_API)?_(?:KEY|TOKEN|SECRET))"
    r")\b\s*(?:=|:)\s*(?P<quote>['\"])?(?P<value>[^#\s'\"]{8,})(?P=quote)?",
    re.IGNORECASE,
)

SAFE_SECRET_VALUES = {
    "example",
    "examples",
    "placeholder",
    "redacted",
    "test",
    "testing",
    "dummy",
    "fake",
    "changeme",
    "change_me",
    "your_key_here",
    "your_token_here",
    "your_secret_here",
    "replace_me",
    "replace-this",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scan release files for obvious private leaks.")
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path.cwd(),
        help="Repository root used for git ls-files scanning.",
    )
    parser.add_argument(
        "--path",
        type=Path,
        action="append",
        help="Scan one or more directories/files directly instead of all git-tracked files.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.repo_root.expanduser().resolve()
    findings = scan_paths(args.path) if args.path else scan_git_tracked(root)

    if not findings:
        return 0

    print("public release safety scan failed:", file=sys.stderr)
    for finding in findings:
        print(f"  {finding.format()}", file=sys.stderr)
    print(f"found {len(findings)} issue(s)", file=sys.stderr)
    return 1


def scan_git_tracked(repo_root: Path) -> list[Finding]:
    completed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=False,
    )
    findings: list[Finding] = []
    for raw_entry in completed.stdout.split(b"\0"):
        if not raw_entry:
            continue
        rel_path = Path(raw_entry.decode("utf-8"))
        absolute = repo_root / rel_path
        if absolute.is_file():
            findings.extend(scan_file(absolute, rel_path))
    return findings


def scan_path(path: Path) -> list[Finding]:
    if path.is_dir():
        findings: list[Finding] = []
        for file_path in sorted(p for p in path.rglob("*") if p.is_file()):
            findings.extend(scan_file(file_path, file_path.relative_to(path)))
        return findings
    if path.is_file():
        return scan_file(path, path)
    raise FileNotFoundError(path)


def scan_paths(paths: list[Path]) -> list[Finding]:
    findings: list[Finding] = []
    for path in paths:
        findings.extend(scan_path(path.expanduser()))
    return findings


def scan_file(file_path: Path, display_path: Path) -> list[Finding]:
    if is_supported_archive(file_path):
        return scan_archive(file_path, display_path)

    return scan_text(read_text_for_scan(file_path), display_path)


def scan_text(text: str, display_path: Path) -> list[Finding]:
    findings: list[Finding] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        findings.extend(scan_line(display_path, line_number, line))
    return findings


def read_text_for_scan(file_path: Path) -> str:
    data = file_path.read_bytes()
    if b"\0" in data:
        return ""
    return data.decode("utf-8", errors="replace")


def is_supported_archive(file_path: Path) -> bool:
    name = file_path.name
    return name.endswith((".tar.gz", ".tgz", ".zip", ".whl"))


def scan_archive(file_path: Path, display_path: Path) -> list[Finding]:
    if file_path.name.endswith((".zip", ".whl")):
        return scan_zip_archive(file_path, display_path)
    return scan_tar_archive(file_path, display_path)


def scan_zip_archive(file_path: Path, display_path: Path) -> list[Finding]:
    findings: list[Finding] = []
    with zipfile.ZipFile(file_path) as archive:
        for member in archive.infolist():
            if member.is_dir():
                continue
            data = archive.read(member)
            if b"\0" in data:
                continue
            member_path = Path(f"{display_path.as_posix()}!{member.filename}")
            findings.extend(scan_text(data.decode("utf-8", errors="replace"), member_path))
    return findings


def scan_tar_archive(file_path: Path, display_path: Path) -> list[Finding]:
    findings: list[Finding] = []
    with tarfile.open(file_path) as archive:
        for member in archive.getmembers():
            if not member.isfile():
                continue
            extracted = archive.extractfile(member)
            if extracted is None:
                continue
            data = extracted.read()
            if b"\0" in data:
                continue
            member_path = Path(f"{display_path.as_posix()}!{member.name}")
            findings.extend(scan_text(data.decode("utf-8", errors="replace"), member_path))
    return findings


def scan_line(display_path: Path, line_number: int, line: str) -> list[Finding]:
    findings: list[Finding] = []
    for pattern in build_text_patterns():
        for match in pattern.regex.finditer(line):
            findings.append(
                Finding(
                    path=display_path,
                    line_number=line_number,
                    label=pattern.label,
                    match=redact(match.group(0)),
                )
            )

    for match in SECRET_ASSIGNMENT.finditer(line):
        value = match.group("value")
        quote = match.group("quote")
        key = match.group("key")
        if is_safe_secret_value(key, value, quoted=quote is not None):
            continue
        findings.append(
            Finding(
                path=display_path,
                line_number=line_number,
                label="secret-like assignment",
                match=redact(match.group(0)),
            )
        )
    return findings


def build_text_patterns() -> list[Pattern]:
    configured_terms = parse_configured_private_terms(os.environ.get(CONFIGURED_TERMS_ENV, ""))
    if not configured_terms:
        return BASE_TEXT_PATTERNS

    escaped_terms = [re.escape(term) for term in configured_terms]
    configured_pattern = Pattern(
        "configured private term",
        re.compile(r"(?<![A-Za-z0-9_])(?:" + "|".join(escaped_terms) + r")(?![A-Za-z0-9_])", re.IGNORECASE),
    )
    return [*BASE_TEXT_PATTERNS, configured_pattern]


def parse_configured_private_terms(raw: str) -> list[str]:
    terms: list[str] = []
    for term in re.split(r"[\n,]", raw):
        cleaned = term.strip()
        if cleaned:
            terms.append(cleaned)
    return terms


def is_safe_secret_value(key: str, value: str, *, quoted: bool) -> bool:
    normalized = value.strip().strip(",)")
    if normalized.lower() in SAFE_SECRET_VALUES:
        return True
    if normalized.startswith(("$", "_", "{")):
        return True
    if not quoted and not key.isupper() and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", normalized):
        return True
    if not quoted and re.search(r"[()[\]{}.]|->", normalized):
        return True
    if normalized.lower() == key.lower():
        return True
    return False


def redact(text: str) -> str:
    if "=" not in text and ":" not in text:
        return text

    key, separator, value = re.split(r"([=:])", text, maxsplit=1)
    if not separator:
        return text

    value = value.strip()
    if len(value) <= 4:
        masked = "***"
    else:
        masked = f"{value[:2]}***{value[-2:]}"
    return f"{key}{separator}{masked}"


if __name__ == "__main__":
    raise SystemExit(main())
