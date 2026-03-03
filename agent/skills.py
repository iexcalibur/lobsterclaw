"""
Skills system — mirrors OpenClaw's skills loader.

Skills are directories in workspace/skills/ each containing a SKILL.md file.
They are loaded and injected into the system prompt so the agent knows about
specialized capabilities and workflows.

Structure:
  workspace/skills/
    my-skill/
      SKILL.md        ← skill description and usage guide
    another-skill/
      SKILL.md

Each SKILL.md can have optional YAML frontmatter:
  ---
  always: true        # Always include regardless of topic
  description: "..."  # One-line description shown in skill list
  ---

Limits (matching OpenClaw):
  max_skills: 50
  max_total_chars: 20_000
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_SKILLS = 50
MAX_TOTAL_CHARS = 20_000
DEFAULT_SKILLS_DIR = Path(__file__).parent.parent / "workspace" / "skills"


@dataclass
class Skill:
    name: str
    path: Path
    content: str
    always: bool = False
    description: str = ""


def _parse_frontmatter(content: str) -> tuple[dict, str]:
    """Parse optional YAML frontmatter from markdown content."""
    if not content.startswith("---"):
        return {}, content

    end = content.find("\n---", 3)
    if end == -1:
        return {}, content

    frontmatter_text = content[3:end].strip()
    body = content[end + 4:].lstrip("\n")

    meta: dict = {}
    for line in frontmatter_text.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if v.lower() == "true":
                meta[k] = True
            elif v.lower() == "false":
                meta[k] = False
            else:
                meta[k] = v
    return meta, body


def load_skills(skills_dir: Path | None = None) -> list[Skill]:
    """Load all skills from the skills directory."""
    directory = skills_dir or DEFAULT_SKILLS_DIR
    if not directory.exists():
        return []

    skills: list[Skill] = []
    for skill_dir in sorted(directory.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue
        try:
            raw = skill_md.read_text(encoding="utf-8")
            meta, body = _parse_frontmatter(raw)
            skill = Skill(
                name=skill_dir.name,
                path=skill_md,
                content=body.strip(),
                always=bool(meta.get("always", False)),
                description=str(meta.get("description", "")),
            )
            skills.append(skill)
            logger.debug("Loaded skill: %s", skill.name)
        except Exception as e:
            logger.warning("Failed to load skill %s: %s", skill_dir.name, e)

    return skills[:MAX_SKILLS]


def format_skills_for_prompt(skills: list[Skill] | None = None) -> str:
    """Format skills into a system prompt section."""
    if skills is None:
        skills = load_skills()
    if not skills:
        return ""

    sections: list[str] = []
    total_chars = 0

    for skill in skills:
        entry = f"### Skill: {skill.name}\n{skill.content}"
        if total_chars + len(entry) > MAX_TOTAL_CHARS:
            logger.debug("Skills truncated at %d chars (limit %d)", total_chars, MAX_TOTAL_CHARS)
            break
        sections.append(entry)
        total_chars += len(entry)

    if not sections:
        return ""

    return (
        "## Available Skills\n\n"
        "The following skills are available to help you with specific tasks:\n\n"
        + "\n\n---\n\n".join(sections)
    )
