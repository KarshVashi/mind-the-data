# Single entry point for every check and task. CI runs `make check`.
SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

UV        ?= uv
DBT_DIR   := transform
DBT       := cd $(DBT_DIR) && $(UV) run --project .. dbt
DBT_FLAGS := --profiles-dir .
# Terraform roots that exist so far; modules are validated through the roots that call them.
TF_ROOTS  := $(wildcard infra/bootstrap infra/envs/prod)

.PHONY: help
help: ## List targets
	@grep -E '^[a-z][a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-16s %s\n", $$1, $$2}'

.PHONY: install
install: ## Install the locked Python environment
	$(UV) sync --locked

# ---- Full gate ---------------------------------------------------------------

.PHONY: check
check: lint typecheck test sql-lint dbt-parse tf-fmt-check tf-validate tf-lint ## Everything CI runs

.PHONY: check-fast
check-fast: lint typecheck test ## ruff, mypy, unit tests

.PHONY: lint
lint:
	$(UV) run ruff check .
	$(UV) run ruff format --check .

.PHONY: typecheck
typecheck:
	$(UV) run mypy

.PHONY: test
test: ## Unit tests
	$(UV) run pytest tests/unit -q

.PHONY: test-all
test-all: ## Unit and integration tests
	$(UV) run pytest tests -q

.PHONY: sql-lint
sql-lint:
	$(UV) run sqlfluff lint $(DBT_DIR)/models

.PHONY: dbt-parse
dbt-parse:
	$(DBT) parse $(DBT_FLAGS)

.PHONY: tf-fmt-check
tf-fmt-check:
	terraform fmt -check -recursive infra

.PHONY: tf-validate
tf-validate:
	@for d in $(TF_ROOTS); do \
		echo "terraform validate $$d"; \
		terraform -chdir=$$d init -backend=false -input=false >/dev/null; \
		terraform -chdir=$$d validate; \
	done

.PHONY: tf-lint
tf-lint:
	@for d in $(TF_ROOTS); do \
		echo "tflint $$d"; \
		tflint --chdir=$$d --init >/dev/null; \
		tflint --chdir=$$d; \
	done

# ---- Formatting ------------------------------------------------------------

.PHONY: fmt
fmt: ## Format Python, Terraform and SQL in place
	$(UV) run ruff check --fix .
	$(UV) run ruff format .
	terraform fmt -recursive infra
	$(UV) run sqlfluff fix --force $(DBT_DIR)/models

# ---- Needs AWS access -------------------------------------------------------

.PHONY: dbt-build-dev
dbt-build-dev: ## dbt build against the dev target (needs AWS read access)
	$(DBT) build --target dev $(DBT_FLAGS)

.PHONY: plan
plan: ## terraform plan for infra/envs/prod (needs AWS access)
	terraform -chdir=infra/envs/prod init -input=false
	terraform -chdir=infra/envs/prod plan -input=false
