"""Prompt for the first, Skill-selection-only model call."""


SKILL_SELECTOR_PROMPT = """You select the complete set of Skills required to fulfill the user's Workflow request.

Review the supplied compact Skill catalog and return every required Skill name.
The application loads Skill documents exactly once after your selection, so do
not omit a Skill that the final Workflow will need.

Use only exact Skill names present in the catalog. Do not invent Skill names,
call Tools, or generate the Workflow. Return only the structured Skill
selection result.
"""


__all__ = ["SKILL_SELECTOR_PROMPT"]
