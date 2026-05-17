# Repository Guidelines

## Project Structure & Module Organization
This repo combines data-processing scripts with a local chat web app.
- `downloadchatmsg_v2.py`: standalone export/download utility.
- `messages_export.json`, `chat_merged.json`: exported/merged message data.
- `frontend/server.py`: Python HTTP backend (OpenAI-compatible request flow).
- `frontend/twin_core.py`: twin/memory logic used by the backend.
- `frontend/static/`: frontend assets (`index.html`, `app.js`, `styles.css`).
- `frontend/data/conversations/`: per-conversation JSON files.
- `frontend/data/twin/`: twin state, memory index, checkpoints.
- `frontend/data/api_requests.jsonl`: request/response audit log.

## Build, Test, and Development Commands
No build system is required; run directly with Python.
- `cd frontend`
- `python server.py`: start local server (default `http://127.0.0.1:9666`).
- `python ../downloadchatmsg_v2.py`: run export script from repo root.
- Optional environment variables before start:
  - `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `OPENAI_MODEL`.

## Coding Style & Naming Conventions
- Python: 4-space indentation, `snake_case` for functions/variables, `UPPER_SNAKE_CASE` for constants.
- JavaScript: `camelCase` for variables/functions; keep DOM helpers small and single-purpose.
- Keep modules focused: API/network logic in `server.py`, memory/twin logic in `twin_core.py`.
- Prefer explicit JSON field names aligned with current data files (`created_at`, `updated_at`, `messages`).

## Testing Guidelines
There is no formal test suite yet in this repository.
- Validate changes by running `python server.py` and exercising the UI manually.
- For backend changes, verify conversation files are written to `frontend/data/conversations/` correctly.
- For API changes, confirm new entries in `frontend/data/api_requests.jsonl` are masked and complete.
- If you add tests, place them under `frontend/tests/` and use `test_*.py` naming.

## Commit & Pull Request Guidelines
Git history is not available in this folder snapshot, so use these conventions:
- Commit format: `type(scope): short summary` (e.g., `fix(server): handle empty model response`).
- Keep commits focused and atomic; avoid mixing data exports with code refactors.
- PRs should include: purpose, key changes, manual test steps, and screenshots for UI updates.
- Reference related issues/tasks and note any data file migrations or format changes.

## Security & Configuration Tips
- Never commit real API keys; use environment variables or local-only settings.
- Treat `frontend/data/` as runtime data; scrub sensitive conversation content before sharing.
- Keep request logs sanitized (Authorization should remain masked).
