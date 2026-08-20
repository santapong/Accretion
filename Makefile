.PHONY: dev-db migrate api ui check test

dev-db:
	docker compose up -d postgres

migrate:
	uv run alembic upgrade head

api:
	uv run uvicorn accretion.api.main:app --reload

ui:
	npm run dev --workspace @accretion/ui

check:
	uv run ruff check .
	uv run mypy src
	npm run check

test:
	PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --no-sync pytest -p pytest_asyncio.plugin
	npm run test
