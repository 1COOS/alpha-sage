API_PORT ?= 7777
WEB_PORT ?= 8888

.PHONY: install init-db dev-api dev-web test test-backend lint build sync-history preflight check

install:
	uv sync --project backend --python 3.12 --extra dev
	pnpm --dir frontend install

init-db:
	cd backend && uv run alembic upgrade head
	cd backend && uv run alpha-sage bootstrap

dev-api:
	cd backend && uv run uvicorn app.main:app --host 127.0.0.1 --port $(API_PORT) --reload

dev-web:
	NEXT_PUBLIC_API_BASE_URL=http://localhost:$(API_PORT) pnpm --dir frontend dev --port $(WEB_PORT)

sync-history:
	cd backend && uv run alpha-sage sync-history --years 5

preflight:
	cd backend && uv run alpha-sage preflight

test-backend:
	cd backend && uv run pytest

test: test-backend
	pnpm --dir frontend test

lint:
	cd backend && uv run ruff check app tests alembic/versions
	cd backend && uv run ruff format --check app tests alembic/versions
	pnpm --dir frontend lint

build:
	pnpm --dir frontend build

check: lint test build
