# memory/ — Detailed Memory Storage

This folder contains per-topic memory files written by the agent.

## How It Works

- `MEMORY.md` (parent folder) holds the most important short-term facts.
- Files in `memory/` hold detailed, topic-specific notes (e.g. project details,
  meeting notes, user preferences, research summaries).
- The agent uses `memory_write` to create/update files here.
- The agent uses `memory_search` to do full-text search across all files.
- The agent uses `memory_get` to read specific files.

## Examples

- `memory/projects.md` — active project notes
- `memory/preferences.md` — detailed user preferences
- `memory/contacts.md` — people the user mentions
- `memory/research/topic-name.md` — saved research notes

## Editing

You can freely edit, add, or delete files in this folder.
Changes take effect immediately on the next agent run.
