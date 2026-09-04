"""The GMGN skills' contract with the Web UI's "AgentOS Crypto Skills" group.

The grouping is derived, not listed anywhere: the Skills page files a shipped
skill under that heading when its manifest says ``category: crypto``, and it
paints the GMGN mark when ``provenance.origin`` says ``gmgn-mit``. Both facts
live in frontmatter, so a new GMGN skill copied from a sibling can silently land
under "AgentOS Normal Skills" wearing the generic glyph and nothing else would
notice. These tests are that notice.
"""

from __future__ import annotations

from pathlib import Path

from agentos.skills.loader import SkillLoader

ROOT = Path(__file__).resolve().parents[1]
BUNDLED = ROOT / "src" / "agentos" / "skills" / "bundled"

EXPECTED_GMGN_SKILLS = {
    "gmgn-cooking",
    "gmgn-holder-analysis",
    "gmgn-market",
    "gmgn-portfolio",
    "gmgn-swap",
    "gmgn-token",
    "gmgn-track",
    "gmgn-wallet-analysis",
    "gmgn-wallet-score",
}


def _gmgn_specs(tmp_path: Path) -> list:
    loader = SkillLoader(bundled_dir=BUNDLED, snapshot_path=tmp_path / "snapshot.json")
    return [s for s in loader.load_all() if s.provenance.origin == "gmgn-mit"]


def test_gmgn_skills_declare_the_crypto_category(tmp_path: Path) -> None:
    specs = _gmgn_specs(tmp_path)
    assert {s.name for s in specs} == EXPECTED_GMGN_SKILLS
    for spec in specs:
        assert spec.metadata is not None, spec.name
        # Lower-cased by the loader; the frontend compares against exactly this.
        assert spec.metadata.category == "crypto", spec.name


def test_each_gmgn_skill_has_its_own_emoji(tmp_path: Path) -> None:
    """The mark is shared, so the emoji is what tells the nine cards apart."""
    specs = _gmgn_specs(tmp_path)
    emojis = {}
    for spec in specs:
        assert spec.metadata is not None, spec.name
        emoji = spec.metadata.emoji.strip()
        assert emoji, spec.name
        assert emoji not in emojis, f"{spec.name} reuses {emojis[emoji]}'s emoji {emoji}"
        emojis[emoji] = spec.name
