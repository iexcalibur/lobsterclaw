"""Tests for the skills system."""
import tempfile
from pathlib import Path

import pytest
from agent.skills import load_skills, format_skills_for_prompt, Skill


def _make_skill_dir(base: Path, name: str, content: str, frontmatter: str = "") -> Path:
    skill_dir = base / name
    skill_dir.mkdir(parents=True)
    if frontmatter:
        (skill_dir / "SKILL.md").write_text(f"---\n{frontmatter}\n---\n\n{content}")
    else:
        (skill_dir / "SKILL.md").write_text(content)
    return skill_dir


def test_load_skills_empty_dir(tmp_path):
    skills = load_skills(tmp_path)
    assert skills == []


def test_load_single_skill(tmp_path):
    _make_skill_dir(tmp_path, "my-skill", "# My Skill\n\nDo something useful.")
    skills = load_skills(tmp_path)
    assert len(skills) == 1
    assert skills[0].name == "my-skill"
    assert "Do something useful" in skills[0].content


def test_load_skill_with_frontmatter(tmp_path):
    _make_skill_dir(
        tmp_path,
        "smart-skill",
        "Do smart things.",
        frontmatter='always: true\ndescription: "A smart skill"',
    )
    skills = load_skills(tmp_path)
    assert len(skills) == 1
    assert skills[0].always is True
    assert skills[0].description == "A smart skill"


def test_skip_dir_without_skill_md(tmp_path):
    (tmp_path / "no-skill").mkdir()
    (tmp_path / "no-skill" / "README.md").write_text("Not a skill")
    skills = load_skills(tmp_path)
    assert skills == []


def test_multiple_skills_sorted(tmp_path):
    _make_skill_dir(tmp_path, "b-skill", "Skill B")
    _make_skill_dir(tmp_path, "a-skill", "Skill A")
    skills = load_skills(tmp_path)
    assert len(skills) == 2
    assert skills[0].name == "a-skill"
    assert skills[1].name == "b-skill"


def test_format_no_skills():
    result = format_skills_for_prompt([])
    assert result == ""


def test_format_with_skills():
    skills = [Skill(name="test", path=Path("/tmp"), content="Test content")]
    result = format_skills_for_prompt(skills)
    assert "test" in result
    assert "Test content" in result
    assert "Available Skills" in result


def test_format_respects_char_limit(tmp_path):
    # Create many large skills
    for i in range(50):
        _make_skill_dir(tmp_path, f"skill-{i:02d}", "x" * 1000)
    skills = load_skills(tmp_path)
    result = format_skills_for_prompt(skills)
    assert len(result) <= 21_000  # Slightly over MAX_TOTAL_CHARS due to headers
