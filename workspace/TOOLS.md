# TOOLS.md — Tool Usage Guidelines

_This file gives the agent guidance on HOW to use tools effectively.
It does not control which tools are available — that is set in .env._

## web_search
- Use for current events, facts you're unsure about, or anything time-sensitive.
- Prefer specific queries over broad ones.
- Always cite the source URL when sharing search results.

## web_fetch
- Use when you have a specific URL and need its full content.
- Works better than web_search for reading documentation, articles, or reports.

## cron
- When scheduling reminders, include enough context in the message so it makes
  sense when it fires (e.g. "Call back John about the proposal from today").
- For one-time reminders: use ISO datetime format (e.g. 2025-03-15T14:30:00).
- For recurring tasks: use cron expression (e.g. "0 9 * * 1" = every Monday at 9am).

## memory_write / memory_search / memory_get
- Save important facts the user shares (name, preferences, project details).
- Search memory before answering questions about the user's past decisions, preferences, or todos.
- Use descriptive keys (e.g. "user_preference_language", "project_alpha_deadline").

## browser
- DANGEROUS — requires BROWSER_ENABLED=true and user approval.
- Always confirm with the user before navigating to sites with login sessions.
- Close the browser when done.

## exec / process
- DANGEROUS — requires EXEC_ENABLED=true and user approval.
- Always show the exact command before running it.
- Prefer specific commands over broad shell scripts.

## tts
- Use when the user asks for a voice message or audio response.
- Keep TTS text clean — no markdown symbols, URLs, or code blocks.
