.PHONY: dev-db migrate api ui check docs-check test acceptance anchors release-gate style-diff-base

dev-db:
	docker compose up -d postgres

migrate:
	uv run alembic upgrade head

api:
	uv run uvicorn accretion.api.main:app --reload

ui:
	npm run dev --workspace @accretion/ui

check:
	uv lock --check
	uv run ruff check .
	uv run mypy src
	uv run --no-sync python scripts/check_docs.py
	uv run --no-sync python scripts/export_contract_schemas.py --check
	npm run check

docs-check:
	uv run --no-sync python scripts/check_docs.py

test:
	PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --no-sync pytest -p pytest_asyncio.plugin
	npm run test

acceptance:
	PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --no-sync python scripts/check_acceptance.py

# Re-address the vitest pointers in docs/acceptance/criteria.toml after moving a test.
# Deliberately not part of `check` or CI: drift has to surface in a failing gate, and the
# repair has to be a decision someone made. Run it, read what moved, then commit.
anchors:
	uv run --no-sync python scripts/sync_frontend_anchors.py --write

release-gate:
	PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --no-sync python scripts/release_gate.py

# Build the pre-migration base the computed-style diff compares against.
#
# `apps/ui/e2e/style-diff.spec.ts` needs TWO builds of the app: this branch on :4173 and the
# merge-base with `develop` on :4174. Without the second one the spec has nothing to compare
# against and skips locally, so this target is the local half of what the `browser` CI job
# does inline.
#
# A worktree rather than a stash or a second clone: it checks out the merge-base commit
# beside the working tree without touching it, which matters because the working tree is
# where the change under review lives.
#
# `node_modules` is SYMLINKED, not installed. `npm ci` in a worktree would take minutes and
# would fight for the same package cache. The consequence is stated rather than hidden: if
# the base commit's `package-lock.json` differs from this branch's, the base is built with
# this branch's dependencies. That makes the diff a comparison of stylesheets rather than of
# dependency trees, which is what it is for; the text-level proof in
# `apps/ui/e2e/cssPort.test.ts` does not depend on it at all.
STYLE_DIFF_BASE_WORKTREE := $(CURDIR)/.worktrees/style-diff-base
STYLE_DIFF_BASE_DIST := $(CURDIR)/.style-diff-base-dist

style-diff-base:
	@set -e; \
	base=$$(git merge-base HEAD develop); \
	echo "merge-base with develop: $$base"; \
	git worktree remove --force $(STYLE_DIFF_BASE_WORKTREE) 2>/dev/null || true; \
	rm -rf $(STYLE_DIFF_BASE_WORKTREE) $(STYLE_DIFF_BASE_DIST); \
	git worktree add --detach $(STYLE_DIFF_BASE_WORKTREE) $$base; \
	ln -s $(CURDIR)/node_modules $(STYLE_DIFF_BASE_WORKTREE)/node_modules; \
	ln -s $(CURDIR)/apps/ui/node_modules $(STYLE_DIFF_BASE_WORKTREE)/apps/ui/node_modules; \
	cd $(STYLE_DIFF_BASE_WORKTREE) && npm run build --workspace @accretion/ui -- \
		--outDir $(STYLE_DIFF_BASE_DIST) --emptyOutDir; \
	echo; \
	echo "base build ready. Run:"; \
	echo "  export STYLE_DIFF_BASE_DIST=$(STYLE_DIFF_BASE_DIST)"
