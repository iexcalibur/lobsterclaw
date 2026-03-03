# Skills

Each skill is a subdirectory containing a `SKILL.md` file.

The agent reads skill descriptions and injects them into its system prompt,
making the skills available as context for how to handle specific tasks.

## Creating a Skill

Create a directory and add a `SKILL.md`:

```
workspace/skills/my-skill/SKILL.md
```

Example `SKILL.md`:

```markdown
# My Skill

Short description of what this skill does.

## Usage

Instructions for the agent on how and when to use this skill.

## Example

Input: ...
Output: ...
```

The agent will see the skill's content in its system prompt and can use it
to improve its responses for related tasks.
