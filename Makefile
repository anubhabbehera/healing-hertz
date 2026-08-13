# healing-hertz — common tasks. Run `make` (or `make help`) for the list.

SHELL       := /bin/bash
BACKEND     := backend
FRONTEND    := frontend
VERSION     ?= 0.1.0
REVISION    ?= $(shell git rev-parse --short HEAD 2>/dev/null || echo unknown)
BUILD_DATE  ?= $(shell date -u +%Y-%m-%dT%H:%M:%SZ)
DEMO_DB     := $(BACKEND)/healing_hertz.demo.db

export VERSION REVISION BUILD_DATE

.DEFAULT_GOAL := help
.PHONY: help setup dev demo stop restart test lint fmt build audit check \
        docker-build docker-up docker-down docker-logs clean clean-demo

## help: show this list
help:
	@echo "healing-hertz targets:"
	@grep -E '^## ' $(MAKEFILE_LIST) | sed 's/## /  /' | column -t -s ':'

## setup: install backend and frontend dependencies
setup:
	cd $(BACKEND) && uv sync --frozen
	cd $(FRONTEND) && npm ci

# ---------------------------------------------------------------- running ---

## dev: run the stack against your real UniFi console (Ctrl-C stops both)
dev:
	./dev.sh

## demo: run the stack with bundled sample data, separate database
demo:
	DEMO_MODE=true DB_PATH=./healing_hertz.demo.db ./dev.sh

## stop: stop a dev/demo stack left running in another terminal
stop:
	@./dev.sh --stop

## restart: stop then start the dev stack
restart: stop dev

# ---------------------------------------------------------------- quality ---

## test: run the backend test suite
test:
	cd $(BACKEND) && uv run pytest -q

## coverage: run the backend suite and write coverage.xml (what CI uploads)
coverage:
	cd $(BACKEND) && uv run pytest -q --cov=app --cov-report=xml --cov-report=term-missing

## lint: ruff (backend) and TypeScript typecheck (frontend)
lint:
	cd $(BACKEND) && uv run ruff check app tests
	cd $(FRONTEND) && npx tsc -b --noEmit

## fmt: auto-fix lint findings and format the backend
fmt:
	cd $(BACKEND) && uv run ruff check --fix app tests && uv run ruff format app tests

## audit: scan dependencies for known vulnerabilities
audit:
	cd $(BACKEND) && uv run pip-audit
	cd $(FRONTEND) && npm audit

## check: lint, test and audit — everything CI would run
check: lint test audit

## build: production build of the frontend bundle
build:
	cd $(FRONTEND) && npm run build

# ----------------------------------------------------------------- docker ---

## docker-build: build both images (labelled with version, revision, date)
docker-build:
	docker compose build

## docker-up: start the containerised stack in the background
docker-up:
	docker compose up -d

## docker-down: stop the containerised stack
docker-down:
	docker compose down

## docker-logs: follow container logs
docker-logs:
	docker compose logs -f

# ------------------------------------------------------------------ chores ---

## clean-demo: delete the demo database only
clean-demo:
	rm -f $(DEMO_DB)

## clean: remove build artifacts, caches and the demo database
clean: clean-demo
	rm -rf $(FRONTEND)/dist
	rm -rf $(BACKEND)/.pytest_cache $(BACKEND)/.ruff_cache
	find $(BACKEND) -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
