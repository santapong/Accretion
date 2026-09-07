"""Validate repository documentation structure, links, and SVG accessibility."""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
MARKDOWN_LINK = re.compile(r"\]\(([^\s)]+)\)")
HTML_TARGET = re.compile(r'(?:src|href)="([^"]+)"')
EXTERNAL_TARGET = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
LOWERCASE_MARKDOWN = re.compile(r"[a-z0-9]+(?:[.-][a-z0-9]+)*\.md")

# These three byte-preserved review sources refer to files outside the historical
# documentation subset. Their links are historical, not live checkout navigation.
# Exempt only the exact imported identities; all other Markdown is checked normally.
FROZEN_REFERENCE_ROOT = DOCS / "sdd/future/v1.1-v1.8/package/company/apps/Accretion"
FROZEN_REFERENCE_DOCUMENTS = {
    FROZEN_REFERENCE_ROOT / "README.md":
        "4134935108685b5aa453b757ba858962409286f453fe75bfe6d2e8f4095a945d",
    FROZEN_REFERENCE_ROOT / "docs/runbooks/p0-runtime.md":
        "89bf92d45df7debb255a3aeac0340a8919d2dd484ce7ab526552d230a8d1c269",
    FROZEN_REFERENCE_ROOT / "docs/releases/v0.4/backlog.md":
        "e19ccaee254918a48187c6b123eb8e80ff440029653be2aea77a0ca4267b6a7c",
}


def tracked_paths(pattern: str) -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z", "--", pattern],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return [ROOT / item.decode() for item in result.stdout.split(b"\0") if item]


def local_targets(text: str) -> list[tuple[int, str]]:
    matches = [*MARKDOWN_LINK.finditer(text), *HTML_TARGET.finditer(text)]
    return sorted((text.count("\n", 0, match.start()) + 1, match.group(1)) for match in matches)


def validate_markdown(files: list[Path]) -> list[str]:
    failures: list[str] = []
    root_documents = [path for path in files if path.parent == DOCS]
    expected_root = {DOCS / "README.md"}
    if set(root_documents) != expected_root:
        unexpected = sorted(
            str(path.relative_to(ROOT)) for path in set(root_documents) - expected_root
        )
        failures.append(f"docs root must contain only README.md; unexpected: {unexpected}")

    for path in files:
        relative = path.relative_to(ROOT)
        if path in FROZEN_REFERENCE_DOCUMENTS:
            if hashlib.sha256(path.read_bytes()).hexdigest() != FROZEN_REFERENCE_DOCUMENTS[path]:
                failures.append(f"{relative}: frozen reference identity changed")
            continue
        if DOCS in path.parents and path.name != "README.md" and DOCS / "sdd" not in path.parents:
            if not LOWERCASE_MARKDOWN.fullmatch(path.name):
                failures.append(f"{relative}: use a lowercase kebab-case Markdown filename")

        text = path.read_text(encoding="utf-8")
        for line, target in local_targets(text):
            target = target.removeprefix("<").removesuffix(">")
            if not target or target.startswith(("#", "/")) or EXTERNAL_TARGET.match(target):
                continue
            target_path = unquote(target.split("#", 1)[0].split("?", 1)[0])
            if not target_path:
                continue
            resolved = (path.parent / target_path).resolve()
            if not resolved.exists():
                failures.append(f"{relative}:{line}: missing local target {target}")
    return failures


def validate_svgs(files: list[Path]) -> list[str]:
    failures: list[str] = []
    for path in files:
        relative = path.relative_to(ROOT)
        try:
            root = ElementTree.parse(path).getroot()
        except ElementTree.ParseError as error:
            failures.append(f"{relative}: invalid XML: {error}")
            continue

        children = {child.tag.rsplit("}", 1)[-1]: child for child in root}
        title = children.get("title")
        description = children.get("desc")
        labelled_by = root.attrib.get("aria-labelledby", "").split()
        expected_ids = {
            element.attrib.get("id")
            for element in (title, description)
            if element is not None and element.attrib.get("id")
        }
        if root.attrib.get("role") != "img":
            failures.append(f'{relative}: root SVG must declare role="img"')
        if title is None or not "".join(title.itertext()).strip():
            failures.append(f"{relative}: SVG must have a non-empty direct <title>")
        if description is None or not "".join(description.itertext()).strip():
            failures.append(f"{relative}: SVG must have a non-empty direct <desc>")
        if len(expected_ids) != 2 or not expected_ids.issubset(labelled_by):
            failures.append(f"{relative}: aria-labelledby must reference title and description IDs")
    return failures


def main() -> int:
    integrity = subprocess.run(
        [sys.executable, str(ROOT / "scripts/validate_future_sdd_package.py"), "--integrity-only"],
        cwd=ROOT,
        check=False,
    )
    if integrity.returncode:
        return integrity.returncode
    markdown = tracked_paths("*.md")
    svgs = tracked_paths("docs/assets/*.svg")
    failures = [*validate_markdown(markdown), *validate_svgs(svgs)]
    if failures:
        print("Documentation check failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print(f"Documentation check passed: {len(markdown)} Markdown files, {len(svgs)} SVGs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
