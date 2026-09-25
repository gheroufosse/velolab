# velolab-api

FastAPI backend for velolab. See repo root README and `docs/` for context.

## Development

```
uv sync
uv run velolab-api          # dev server with reload, http://localhost:8000
uv run ruff check .
uv run ruff format --check .
uv run ty check
uv run pytest
```
