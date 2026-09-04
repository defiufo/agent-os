"""A bundled skill's script has to be where its SKILL.md says it is.

Nothing else checks this. A skill vendored from upstream arrives pointing at
wherever upstream kept its helper — ``~/.claude/skills/<name>/analyze.py`` in
the case that prompted these tests — and that path exists on nobody's machine
once the skill ships inside the wheel. The manifest still parses, the skill
still loads, the Skills page still lists it; the only symptom is the agent
running the documented command and getting "No such file or directory" at the
one moment a user asked it for something.

``{baseDir}`` is what the tool layer substitutes for the skill's own directory,
so it is the only correct way for instruction text to reach a shipped script.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from agentos.skills.loader import SkillLoader

ROOT = Path(__file__).resolve().parents[1]
BUNDLED = ROOT / "src" / "agentos" / "skills" / "bundled"

#: ``{baseDir}/scripts/analyze.py`` and friends, stopping before the trailing
#: punctuation that ends a sentence or closes a code span.
BASE_DIR_REF = re.compile(r"\{baseDir\}/([A-Za-z0-9_./-]+)")

#: A ``python3 …/<something>.py`` invocation that reaches the script through an
#: absolute or home-anchored path instead of ``{baseDir}``.
HOME_ANCHORED_SCRIPT = re.compile(r"python3?\s+(~|/Users/|/home/)[^\s`]*\.py")


def _skill_dirs() -> list[Path]:
    return sorted(p for p in BUNDLED.iterdir() if p.is_dir() and (p / "SKILL.md").is_file())


def test_every_basedir_script_reference_resolves() -> None:
    """Every ``{baseDir}/…`` path in a bundled SKILL.md points at a real file."""
    missing: list[str] = []
    checked = 0

    for skill_dir in _skill_dirs():
        body = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        for raw in sorted(set(BASE_DIR_REF.findall(body))):
            target = raw.rstrip(".,)`")
            checked += 1
            if not (skill_dir / target).exists():
                missing.append(f"{skill_dir.name}: {{baseDir}}/{target}")

    assert checked, "no {baseDir} references found — the regex stopped matching"
    assert not missing, "SKILL.md points at scripts that do not ship: " + "; ".join(missing)


def test_gmgn_skills_reach_their_scripts_through_basedir(tmp_path: Path) -> None:
    """No GMGN skill invokes a script through the author's own home directory.

    The GMGN skills are vendored, so this is where an upstream path survives a
    copy. ``senior-unilp-manager`` is deliberately not covered: it writes a
    standalone ``tick.sh`` for cron, which runs with no skill context and so
    cannot use ``{baseDir}``.
    """
    loader = SkillLoader(bundled_dir=BUNDLED, snapshot_path=tmp_path / "snapshot.json")
    gmgn = [s for s in loader.load_all() if s.provenance.origin == "gmgn-mit"]
    assert gmgn, "no gmgn-mit skills loaded"

    offenders: list[str] = []
    for spec in gmgn:
        body = (BUNDLED / spec.name / "SKILL.md").read_text(encoding="utf-8")
        for hit in HOME_ANCHORED_SCRIPT.findall(body):
            offenders.append(f"{spec.name}: {hit}")

    assert not offenders, "hard-coded script paths survived vendoring: " + "; ".join(offenders)


def test_vendored_gmgn_scripts_are_runnable_as_files() -> None:
    """The lifted analyzers take argv and parse — no ``<FILL_IN_*>`` left behind.

    Upstream ships some of these as inline heredocs whose constants the agent is
    expected to string-replace. Once lifted into ``scripts/``, a surviving
    placeholder is a ``SyntaxError`` at the only moment it matters.
    """
    scripts = sorted(BUNDLED.glob("gmgn-*/scripts/*.py"))
    assert scripts, "expected vendored GMGN helper scripts"

    for script in scripts:
        source = script.read_text(encoding="utf-8")
        rel = script.relative_to(BUNDLED)
        assert "<FILL_IN_" not in source, f"{rel} still carries an unsubstituted placeholder"
        ast.parse(source, filename=str(script))
