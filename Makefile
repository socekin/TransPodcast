.PHONY: setup check doc-check secret-check security-check test lint typecheck build dev api worker generate-api

setup:
	uv sync --all-groups
	npm --prefix apps/owner-console ci
	npm --prefix workers/publication ci

lint:
	uv run ruff check .
	sh -n scripts/dev.sh deploy/*.sh

typecheck:
	uv run mypy src
	npm --prefix apps/owner-console run typecheck
	npm --prefix workers/publication run check

test: build
	uv run pytest -q -p no:cacheprovider \
		--cov=transpodcast --cov-report=term:skip-covered --cov-fail-under=80
	npm --prefix apps/owner-console test
	npm --prefix apps/owner-console run test:sites
	npm --prefix workers/publication test

build:
	npm --prefix apps/owner-console run build

secret-check:
	uv run python scripts/check_secrets.py

doc-check:
	uv run python scripts/check_markdown_links.py

check: lint typecheck test doc-check secret-check

security-check: secret-check
	mkdir -p .cache/pip-audit
	uv export --frozen --all-groups --no-emit-project \
		--format requirements-txt > .cache/audit-requirements.txt
	uv run pip-audit -r .cache/audit-requirements.txt --disable-pip \
		--cache-dir .cache/pip-audit --progress-spinner off
	npm --prefix apps/owner-console audit --audit-level=high
	npm --prefix workers/publication audit --audit-level=high

dev: build
	./scripts/dev.sh

api:
	uv run transpodcast-api

worker:
	uv run transpodcast-worker

generate-api:
	npm --prefix apps/owner-console run generate:api
