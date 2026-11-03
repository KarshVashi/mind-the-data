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
# Hand-written SQL; macros are skipped by the dbt templater anyway.
SQL_DIRS  := $(DBT_DIR)/models $(DBT_DIR)/tests $(DBT_DIR)/analyses

.PHONY: help
help: ## List targets
	@grep -E '^[a-z][a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-16s %s\n", $$1, $$2}'

.PHONY: install
install: ## Install the locked Python environment and git pre-commit hook
	$(UV) sync --locked
	$(UV_RUN) pre-commit install

# ---- Full gate ---------------------------------------------------------------

.PHONY: check
check: lint typecheck test-all sql-lint dbt-parse tf-fmt-check tf-validate tf-lint ## Everything CI runs

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
	$(UV_RUN) sqlfluff lint $(SQL_DIRS)

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
		tflint --chdir=$$d --config=$(CURDIR)/.tflint.hcl --init >/dev/null; \
		tflint --chdir=$$d --config=$(CURDIR)/.tflint.hcl; \
	done

# ---- Formatting ------------------------------------------------------------

.PHONY: fmt
fmt: ## Format Python, Terraform and SQL in place
	$(UV_RUN) ruff check --fix .
	$(UV_RUN) ruff format .
	terraform fmt -recursive infra
	$(UV_RUN) sqlfluff fix $(SQL_DIRS)

# ---- Needs AWS access -------------------------------------------------------

DBT_DEV_VARS := DBT_ATHENA_S3_STAGING_DIR_DEV DBT_ATHENA_S3_DATA_DIR_DEV DBT_ATHENA_WORKGROUP_DEV

.PHONY: dbt-build-dev
dbt-build-dev: ## dbt build against the dev target (needs AWS read access)
	@missing=""; for v in $(DBT_DEV_VARS); do [ -n "$${!v:-}" ] || missing="$$missing $$v"; done; \
	if [ -n "$$missing" ]; then echo "dbt-build-dev: set$$missing (see env.example)" >&2; exit 1; fi
	$(DBT) build --target dev $(DBT_FLAGS)

.PHONY: plan
plan: ## terraform plan for infra/envs/prod (needs AWS read access and TF_STATE_BUCKET)
	@[ -n "$${TF_STATE_BUCKET:-}" ] || { echo "plan: set TF_STATE_BUCKET (bootstrap output state_bucket)" >&2; exit 1; }
	terraform -chdir=infra/envs/prod init -input=false -backend-config="bucket=$$TF_STATE_BUCKET"
	terraform -chdir=infra/envs/prod plan -input=false -lock=false
