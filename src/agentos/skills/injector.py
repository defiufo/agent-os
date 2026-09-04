"""Injects active skill content into system prompts — full/compact modes."""

from __future__ import annotations

from dataclasses import dataclass, field

from agentos.skills.types import SkillLayer, SkillSpec

#: Character budget for the injected skills block when nothing configures one.
#: Kept here rather than at each call site: the same number is the default for
#: ``skills.max_skills_prompt_chars`` and the fallback the turn pipeline uses
#: when a turn arrives without a skills config, and those two drifting apart is
#: what silently forced default installs into name-only mode. It has to stay
#: ahead of the shipped set's own descriptions — see
#: ``test_the_lifted_value_fits_the_shipped_skills_in_full_mode``, which is what
#: catches the budget going stale as bundled skills are added.
DEFAULT_MAX_SKILLS_PROMPT_CHARS = 26_000

#: How the block was rendered, widest first. Recorded per turn so a fall to a
#: narrower mode is a visible fact rather than something an operator has to
#: infer from a character count.
RENDER_MODE_FULL = "full"
RENDER_MODE_FULL_TRUNCATED = "full_truncated"
RENDER_MODE_COMPACT = "compact"
RENDER_MODE_COMPACT_TRUNCATED = "compact_truncated"

#: Shortest description worth rendering. Below roughly this a description stops
#: being a sentence and starts being a fragment, at which point names-only is
#: the more honest render.
MIN_DESCRIPTION_CHARS = 120

# Highest precedence first — the same order that decides a name collision. When
# the budget forces a cut it lands on the tail, so a skill in a writable skills
# path outlives a shipped one, and `extra` (read-only config dirs) goes first.
_LAYER_PRECEDENCE: dict[SkillLayer, int] = {
    SkillLayer.WORKSPACE: 0,
    SkillLayer.PROJECT: 1,
    SkillLayer.PERSONAL: 2,
    SkillLayer.MANAGED: 3,
    SkillLayer.BUNDLED: 4,
    SkillLayer.EXTRA: 5,
}
_UNKNOWN_LAYER_RANK = len(_LAYER_PRECEDENCE)


def _escape_xml(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _layer_rank(skill: SkillSpec) -> int:
    return _LAYER_PRECEDENCE.get(skill.layer, _UNKNOWN_LAYER_RANK)


def _shorten(text: str, limit: int) -> str:
    """Cut ``text`` to ``limit`` characters, preferring a word boundary."""
    if limit <= 0 or len(text) <= limit:
        return text
    cut = text[: limit - 1].rstrip()
    space = cut.rfind(" ")
    if space >= limit // 2:
        cut = cut[:space].rstrip()
    return f"{cut}…"


@dataclass(frozen=True)
class SkillsBlock:
    """A rendered skills block plus what rendering it cost.

    ``mode`` and ``description_max_chars`` exist because the budget decision
    used to leave no trace: ``skills_prompt_chars`` shrinking by 20k looks
    identical to an operator uninstalling skills, and nothing recorded that
    every description had just been dropped.
    """

    text: str
    dropped: list[str] = field(default_factory=list)
    mode: str = RENDER_MODE_FULL
    description_max_chars: int | None = None

    @property
    def degraded(self) -> bool:
        """Whether the budget cost this block descriptions or entries."""
        return self.mode != RENDER_MODE_FULL


class SkillInjector:
    """Injects skill content into system prompts with budget control."""

    def inject_full(
        self,
        system_prompt: str,
        skills: list[SkillSpec],
        *,
        description_max_chars: int | None = None,
        skill_list_tool: bool = False,
    ) -> str:
        """Full mode: name + description for each skill.

        ``description_max_chars`` shortens each description to fit a budget that
        the untrimmed block overruns — still far more use to the model than the
        name-only fallback, which is what this used to degrade to.
        """
        visible = [s for s in skills if not s.disable_model_invocation]
        if not visible:
            return system_prompt

        lines = [
            "\n\n## Skills",
            "Skills are task playbooks written for this install. They carry the "
            "endpoints, commands, conventions, and known pitfalls that a "
            "general-purpose approach misses.",
            "Skill names are identifiers for `skill_view`; they are not callable tools.",
            "Read <available_skills> before answering. When an entry relates to the "
            'request — even partially — call skill_view(name="<skill_name>") to load '
            "its instructions and follow them, then use only the tools available in "
            "this session.",
            # Bias deliberately toward loading. The two failure modes are not
            # symmetric: loading a skill you did not need wastes a little context,
            # while skipping one that encoded the right steps produces a confidently
            # wrong answer. A skill also encodes how the user wants the task done
            # here, which is not something the model can infer from the request.
            "Lean toward loading. Load a skill even for a task you already know how "
            "to do — it defines how that task should be done in this install.",
        ]
        if description_max_chars is not None:
            # Say so, or a clipped description reads as the whole description and
            # the model rules out a skill on half a sentence.
            lines.append(
                "The descriptions below are shortened to fit. They are enough to "
                "judge relevance, not to work from — skill_view returns the full text."
            )
            if skill_list_tool:
                lines.append(
                    "`skill_list` returns every skill's untrimmed description in one call."
                )
        lines.extend(
            [
                "Answer without a skill only when no entry relates to the request.",
                "",
                "<available_skills>",
            ]
        )
        for s in visible:
            description = s.description
            if description_max_chars is not None:
                description = _shorten(description, description_max_chars)
            lines.append("  <skill>")
            lines.append(f"    <name>{_escape_xml(s.name)}</name>")
            lines.append(f"    <description>{_escape_xml(description)}</description>")
            lines.append("  </skill>")
        lines.append("</available_skills>")
        return system_prompt + "\n".join(lines)

    def inject_compact(
        self,
        system_prompt: str,
        skills: list[SkillSpec],
        *,
        skill_list_tool: bool = False,
    ) -> str:
        """Compact mode: name only (saves tokens). Use skill_view to read full content."""
        visible = [s for s in skills if not s.disable_model_invocation]
        if not visible:
            return system_prompt

        lines = [
            "\n\nSkills are task playbooks written for this install. Only their names "
            "are listed below — each one's description and instructions live inside it.",
            "Skill names are identifiers for `skill_view`; they are not callable tools.",
        ]
        # Compact mode strips the descriptions, so the model has nothing to judge
        # relevance against but a name. Telling it to load "only on a clear match"
        # would then be an instruction it cannot follow: no name clearly matches
        # anything. Point at whichever route back to the descriptions exists —
        # asking for a skill_view per plausible name is a route the model will not
        # take once the list is dozens of entries long.
        if skill_list_tool:
            lines.append(
                "A name alone is not enough to rule a skill out. Call `skill_list` "
                "first — one call returns every skill's description — then "
                'skill_view(name="<skill_name>") to load the one that matches.'
            )
        else:
            lines.append(
                "A name alone is not enough to rule a skill out, so call skill_view(name="
                '"<skill_name>") on any entry that plausibly relates to the request before '
                "concluding it does not apply."
            )
        lines.extend(["", "<available_skills>"])
        for s in visible:
            lines.append("  <skill>")
            lines.append(f"    <name>{_escape_xml(s.name)}</name>")
            lines.append("  </skill>")
        lines.append("</available_skills>")
        return system_prompt + "\n".join(lines)

    def render(
        self,
        system_prompt: str,
        skills: list[SkillSpec],
        max_chars: int = DEFAULT_MAX_SKILLS_PROMPT_CHARS,
        *,
        skill_list_tool: bool = False,
    ) -> SkillsBlock:
        """Render the widest skills block that fits ``max_chars``.

        Four steps, each narrower than the last: untrimmed descriptions, then
        descriptions shortened to the longest length that fits, then names only,
        then fewer names. Only the last loses a skill, so overrunning the budget
        by one entry costs a little description text rather than every
        description in the block.
        """
        if not skills:
            return SkillsBlock(text=system_prompt)

        def spend(text: str) -> int:
            return len(text) - len(system_prompt)

        untrimmed = self.inject_full(system_prompt, skills, skill_list_tool=skill_list_tool)
        if spend(untrimmed) <= max_chars:
            return SkillsBlock(text=untrimmed)

        # Widest cap that fits, not the first rung of a fixed ladder: the budget
        # is what the operator agreed to spend, and settling for 320 chars where
        # 451 also fitted (measured on a 58-skill install) leaves description
        # text unbought and clips a dozen skills that did not need clipping.
        # Rendered length is monotonic in the cap — a wider cap can only lengthen
        # a description — so bisect it.
        #
        # The search stops one short of the longest description, so a cap that
        # would clip nothing is never chosen: it is the `untrimmed` render plus a
        # "descriptions are shortened" line that would not be true.
        visible = [s for s in skills if not s.disable_model_invocation]
        longest = max((len(s.description) for s in visible), default=0)
        lo, hi = MIN_DESCRIPTION_CHARS, longest - 1
        best: tuple[int, str] | None = None
        while lo <= hi:
            mid = (lo + hi) // 2
            candidate = self.inject_full(
                system_prompt,
                skills,
                description_max_chars=mid,
                skill_list_tool=skill_list_tool,
            )
            if spend(candidate) <= max_chars:
                best = (mid, candidate)
                lo = mid + 1
            else:
                hi = mid - 1
        if best is not None:
            return SkillsBlock(
                text=best[1],
                mode=RENDER_MODE_FULL_TRUNCATED,
                description_max_chars=best[0],
            )

        compact = self.inject_compact(system_prompt, skills, skill_list_tool=skill_list_tool)
        if len(compact) - len(system_prompt) <= max_chars:
            return SkillsBlock(text=compact, mode=RENDER_MODE_COMPACT)

        # Budget exceeded even in compact — truncate skills. Sort by layer
        # precedence first (stable, so within-layer order is untouched) so the
        # cut lands on bundled skills instead of whatever the operator installed.
        ordered = sorted(visible, key=_layer_rank)
        lo, hi = 0, len(ordered)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            test = self.inject_compact(
                system_prompt, ordered[:mid], skill_list_tool=skill_list_tool
            )
            if len(test) - len(system_prompt) <= max_chars:
                lo = mid
            else:
                hi = mid - 1
        # If the safety header itself exceeds an extremely small budget, keep
        # one compact skill entry rather than dropping the whole skills section.
        # Losing the guard makes skill names more likely to be mistaken for tools.
        kept = max(lo, 1)
        return SkillsBlock(
            text=self.inject_compact(
                system_prompt, ordered[:kept], skill_list_tool=skill_list_tool
            ),
            dropped=[s.name for s in ordered[kept:]],
            mode=RENDER_MODE_COMPACT_TRUNCATED,
        )
