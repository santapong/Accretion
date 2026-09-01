.PHONY: dev-db migrate api ui check docs-check test acceptance release-gate

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
	uv run --no-sync python scripts/check_docs.py
	npm run check

docs-check:
	uv run --no-sync python scripts/check_docs.py

test:
	PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --no-sync pytest -p pytest_asyncio.plugin
	npm run test

acceptance:
	PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --no-sync python scripts/check_acceptance.py

release-gate:
	PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --no-sync python scripts/release_gate.py
