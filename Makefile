# Single entry point for every check and task. CI runs `make check`.
SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

UV        ?= uv
# --locked: fail if uv.lock is out of date instead of quietly re-resolving.
UV_RUN    := $(UV) run --locked
DBT_DIR   := transform
DBT       := cd $(DBT_DIR) && $(UV_RUN) --project .. dbt
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
	$(UV_RUN) ruff check .
	$(UV_RUN) ruff format --check .

.PHONY: typecheck
typecheck:
	$(UV_RUN) mypy

.PHONY: test
test: ## Unit tests
	$(UV_RUN) pytest tests/unit -q

.PHONY: test-all
test-all: ## Unit and integration tests
	$(UV_RUN) pytest tests -q

.PHONY: sql-lint
sql-lint:
	$(UV_RUN) sqlfluff lint $(DBT_DIR)/models

.PHONY: dbt-parse
dbt-parse:
	$(DBT) parse $(DBT_FLAGS)

.PHONY: tf-fmt-check
tf-fmt-check:
	terraform fmt -check -recursive infra

.PHONY: tf-validate
tf-validate:
	@[ -n "$(TF_ROOTS)" ] || echo "terraform validate: no terraform roots yet, skipping"
	@for d in $(TF_ROOTS); do \
		echo "terraform validate $$d"; \
		terraform -chdir=$$d init -backend=false -input=false >/dev/null; \
		terraform -chdir=$$d validate; \
	done

.PHONY: tf-lint
tf-lint:
	@[ -n "$(TF_ROOTS)" ] || echo "tflint: no terraform roots yet, skipping"
	@for d in $(TF_ROOTS); do \
		echo "tflint $$d"; \
		tflint --chdir=$$d --init >/dev/null; \
		tflint --chdir=$$d; \
	done

# ---- Formatting ------------------------------------------------------------

.PHONY: fmt
fmt: ## Format Python, Terraform and SQL in place
	$(UV_RUN) ruff check --fix .
	$(UV_RUN) ruff format .
	terraform fmt -recursive infra
	$(UV_RUN) sqlfluff fix $(DBT_DIR)/models

# ---- Needs AWS access -------------------------------------------------------

DBT_DEV_VARS := DBT_ATHENA_S3_STAGING_DIR_DEV DBT_ATHENA_S3_DATA_DIR_DEV DBT_ATHENA_WORKGROUP_DEV

.PHONY: dbt-build-dev
dbt-build-dev: ## dbt build against the dev target (needs AWS read access)
	@missing=""; for v in $(DBT_DEV_VARS); do [ -n "$${!v:-}" ] || missing="$$missing $$v"; done; \
	if [ -n "$$missing" ]; then echo "dbt-build-dev: set$$missing (see .env.example)" >&2; exit 1; fi
	$(DBT) build --target dev $(DBT_FLAGS)

.PHONY: plan
plan: ## terraform plan for infra/envs/prod (needs AWS access)
	terraform -chdir=infra/envs/prod init -input=false
	terraform -chdir=infra/envs/prod plan -input=false
