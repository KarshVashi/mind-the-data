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
# Hand-written SQL; macros are linted as rendered inside the models that call them.
SQL_DIRS  := $(DBT_DIR)/models $(DBT_DIR)/tests $(DBT_DIR)/analyses

# One provider download shared by every checkout and worktree, instead of
# each .terraform/ holding its own copy (the AWS provider alone is ~700 MB).
# Terraform refuses to start if the directory is missing. Terraform does not
# promise the cache is safe for concurrent `terraform init`, so avoid running
# make check in several checkouts at once, or set TF_PLUGIN_CACHE_DIR= (empty)
# to turn the cache off.
export TF_PLUGIN_CACHE_DIR ?= $(HOME)/.terraform.d/plugin-cache
$(if $(TF_PLUGIN_CACHE_DIR),$(shell mkdir -p "$(TF_PLUGIN_CACHE_DIR)"))

# With only AWS_ACCOUNT_ID set, the other AWS names follow the naming
# convention in infra/ (docs/runbook.md, Repository variables). Non-empty
# values win; empty ones (as copied from env.example) are filled in.
derive = $(if $(strip $($(1))),,$(eval export $(1) := $(2)))
ifneq ($(AWS_ACCOUNT_ID),)
$(call derive,TF_STATE_BUCKET,mtd-tfstate-$(AWS_ACCOUNT_ID))
$(call derive,DBT_ATHENA_S3_STAGING_DIR_DEV,s3://mind-the-data-athena-results-$(AWS_ACCOUNT_ID)/dev/)
$(call derive,DBT_ATHENA_S3_DATA_DIR_DEV,s3://mind-the-data-curated-$(AWS_ACCOUNT_ID)/dev/)
$(call derive,DBT_ATHENA_WORKGROUP_DEV,mind-the-data-dev)
$(call derive,DBT_ATHENA_SCHEMA_DEV,mind_the_data_dev)
endif

.PHONY: help
help: ## List targets
	@grep -E '^[a-z][a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-24s %s\n", $$1, $$2}'

.PHONY: install
install: ## Install the locked Python environment and git pre-commit hook
	$(UV) sync --locked
	$(UV_RUN) pre-commit install

# ---- Full gate ---------------------------------------------------------------

.PHONY: check
check: lint typecheck test-all web-test action-pins handlers sql-lint dbt-parse tf-fmt-check tf-validate tf-lint ## Everything CI runs

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

# The Should I Run prototype (web/should-i-run, ADR-018) is plain ES modules
# with no build step; its pure logic is tested with Node's own runner.
.PHONY: web-test
web-test: ## JS unit tests for web/ (node --test)
	node --test web/

.PHONY: action-pins
action-pins: ## Every GitHub Action pinned to a commit SHA with a version comment
	$(UV_RUN) python scripts/check_action_pins.py .github

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

# ---- Lambda package -----------------------------------------------------------

LAMBDA_BUILD := build/lambda
LAMBDA_REQS  := build/lambda-requirements.txt
LAMBDA_ZIP   := dist/lambda.zip
# boto3 and botocore are bundled at their locked versions rather than taken
# from the runtime: the writer relies on S3 conditional writes (IfNoneMatch),
# which older runtime SDKs reject, and the bundled urllib3 would shadow the
# runtime's copy anyway.
#
# The export is the complete pinned set, so pip installs it with --no-deps and
# resolves nothing again. config/ (line and station lists, read from
# CONFIG_DIR) is copied when present.

.PHONY: package
package: ## Build dist/lambda.zip for the arm64 python3.12 Lambdas
	rm -rf $(LAMBDA_BUILD) $(LAMBDA_ZIP)
	mkdir -p $(LAMBDA_BUILD) dist
	$(UV) export --no-dev --no-hashes --no-emit-project --frozen --quiet \
		-o $(LAMBDA_REQS)
	$(UV) pip install --quiet --no-deps --python 3.12 --target $(LAMBDA_BUILD) \
		--python-platform aarch64-manylinux_2_28 --python-version 3.12 \
		--only-binary :all: -r $(LAMBDA_REQS)
	cp -R src/mind_the_data $(LAMBDA_BUILD)/mind_the_data
	if [ -d config ]; then mkdir -p $(LAMBDA_BUILD)/config && cp -R config/. $(LAMBDA_BUILD)/config/; fi
	$(UV) run --no-project --python 3.12 python scripts/zip_lambda.py $(LAMBDA_BUILD) $(LAMBDA_ZIP)

# Every handler Terraform names must import from the package that deploy
# builds, so a schedule cannot go live before the code it calls. The infra
# workflow runs this before plan and apply.
.PHONY: handlers
# Like package and tflint --init, it downloads (wheels), so make check needs
# the network. Host builds of compiled wheels are checked against uv.lock's hashes.
LOCK_REQS := build/lock-requirements.txt
handlers: package ## Check every Terraform Lambda handler imports from dist/lambda.zip
	$(UV) export --no-dev --no-emit-project --frozen --quiet -o $(LOCK_REQS)
	$(UV) run --no-project --python 3.12 python scripts/check_handlers.py \
		$(LAMBDA_ZIP) $(LOCK_REQS) infra/envs/prod

# ---- Needs AWS access -------------------------------------------------------

DBT_DEV_VARS := DBT_ATHENA_S3_STAGING_DIR_DEV DBT_ATHENA_S3_DATA_DIR_DEV DBT_ATHENA_WORKGROUP_DEV DBT_ATHENA_SCHEMA_DEV

.PHONY: dbt-build-dev
dbt-build-dev: ## dbt build against the dev target (needs AWS read access)
	@missing=""; for v in $(DBT_DEV_VARS); do [ -n "$${!v:-}" ] || missing="$$missing $$v"; done; \
	if [ -n "$$missing" ]; then echo "dbt-build-dev: set$$missing (see env.example)" >&2; exit 1; fi
	$(DBT) build --target dev $(DBT_FLAGS)

# The bootstrap runs from a temporary worktree of origin/main as mtd-admin,
# so the working checkout never switches branch (scripts/bootstrap.sh).
# bootstrap-apply and bootstrap-migrate-state are for the owner only.

.PHONY: bootstrap-plan
bootstrap-plan: ## Plan infra/bootstrap at origin/main as mtd-admin (read-only)
	scripts/bootstrap.sh plan

.PHONY: bootstrap-apply
bootstrap-apply: ## Owner only: apply infra/bootstrap at origin/main as mtd-admin, after a yes
	scripts/bootstrap.sh apply

.PHONY: bootstrap-migrate-state
bootstrap-migrate-state: ## Owner only, once: move local bootstrap state into the state bucket
	scripts/bootstrap.sh migrate-state

# ---- Ops ---------------------------------------------------------------------

.PHONY: status
status: ## PRs, workflow runs, alarms, raw freshness, ops issues (read-only, profile mtd-dev)
	@$(UV_RUN) python scripts/status.py

.PHONY: prune-worktrees
prune-worktrees: ## Remove finished worktrees in WORKTREE_DIR (default .worktrees; DRY_RUN=1 to preview)
	scripts/prune_worktrees.sh $(if $(DRY_RUN),--dry-run,)

.PHONY: plan
plan: package ## terraform plan for infra/envs/prod (needs AWS read access and TF_STATE_BUCKET)
	@[ -n "$${TF_STATE_BUCKET:-}" ] || { echo "plan: set AWS_ACCOUNT_ID or TF_STATE_BUCKET (see env.example)" >&2; exit 1; }
	terraform -chdir=infra/envs/prod init -input=false -backend-config="bucket=$$TF_STATE_BUCKET"
	terraform -chdir=infra/envs/prod plan -input=false -lock=false

# >>> merge gate (protected: config/merge_policy.yaml)
# Low-risk pull requests can be merged without the owner (ADR-011). The gate
# always runs origin/main's copy of its code, policy and lock file, exported
# to a temporary directory, because a feature branch must not be able to
# loosen its own gate and the working tree may be on any branch. `python -I`
# keeps the working tree off sys.path, the environment is built from main's
# uv.lock without the project, and the gate loads its module by file path.
# MERGE_GATE_REF lets merge-check try a proposed gate before it merges;
# `merge` always uses origin/main. Changes to this block need the owner.
# Full refs, and a fetch without tags, so a tag or local branch called
# origin/main cannot stand in for main.
MERGE_GATE_MAIN := refs/remotes/origin/main
MERGE_GATE_REF ?= $(MERGE_GATE_MAIN)
define merge_gate
	@[[ "$(PR)" =~ ^[0-9]+$$ ]] || { echo "usage: make $@ PR=<number>" >&2; exit 2; }
	@git fetch --quiet --no-tags origin +refs/heads/main:$(MERGE_GATE_MAIN)
	@# This recipe comes from the working tree. It only stands for main's if
	@# this block matches the copy being exported, so refuse otherwise.
	@diff -q <(sed -n '/^# >>> merge gate/,/^# <<< merge gate/p' Makefile) \
		<(git show "$(1):Makefile" | sed -n '/^# >>> merge gate/,/^# <<< merge gate/p') \
		>/dev/null || { echo "merge gate: this Makefile's merge block differs from $(1)'s;" \
		"run from a checkout of main" >&2; exit 2; }
	@tmp=$$(mktemp -d); trap 'rm -rf "$$tmp"' EXIT; \
	git archive "$(1)" scripts/merge_gate.py src/mind_the_data/ops config/merge_policy.yaml \
		pyproject.toml uv.lock | tar -x -C "$$tmp"; \
	export UV_PROJECT_ENVIRONMENT="$$tmp/.venv"; unset UV_PYTHON VIRTUAL_ENV; \
	uv sync --quiet --locked --no-dev --no-install-project --project "$$tmp"; \
	uv run --quiet --locked --no-sync --project "$$tmp" \
		python -I "$$tmp/scripts/merge_gate.py" "$(PR)" $(2) --source "$(1)"
endef

.PHONY: merge-check
merge-check: ## Dry-run the merge gate for PR=<n> (gate code from origin/main)
	$(call merge_gate,$(MERGE_GATE_REF),--dry-run)

.PHONY: merge
merge: ## Merge PR=<n> if the gate, run from origin/main, allows it
	$(call merge_gate,$(MERGE_GATE_MAIN),--merge)
# <<< merge gate
