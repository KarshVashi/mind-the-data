#!/usr/bin/env bash
# Plans or applies infra/bootstrap as it is on origin/main, from a temporary
# git worktree, so the checkout you work in never changes branch. For the
# owner only (human step H6, docs/runbook.md); CI never applies the bootstrap.
#
# Usage: scripts/bootstrap.sh plan | apply | migrate-state
#
#   plan           Read-only plan.
#   apply          Plan, show it, ask for "yes", apply exactly that plan, then
#                  set the repository variables from the outputs.
#   migrate-state  One time only: move the old local state
#                  (infra/bootstrap/terraform.tfstate in this checkout, or
#                  LOCAL_STATE) into the state bucket. Applies nothing.
#
# Environment:
#   BOOTSTRAP_PROFILE  AWS profile (default mtd-admin)
#   BOOTSTRAP_REF      git ref to run (default origin/main)
#   ALERT_EMAIL        budget alert address (default: the ALERT_EMAIL
#                      repository variable, read with gh)
set -euo pipefail

action="${1:-}"
case "$action" in
  plan | apply | migrate-state) ;;
  *)
    echo "usage: $0 plan|apply|migrate-state" >&2
    exit 2
    ;;
esac

profile="${BOOTSTRAP_PROFILE:-mtd-admin}"
ref="${BOOTSTRAP_REF:-origin/main}"
repo_root="$(git rev-parse --show-toplevel)"
state_key="bootstrap/terraform.tfstate"

git -C "$repo_root" fetch --quiet origin

if ! aws sts get-caller-identity --profile "$profile" >/dev/null 2>&1; then
  aws sso login --profile "$profile"
fi
account="$(aws sts get-caller-identity --profile "$profile" --query Account --output text)"
bucket="mtd-tfstate-${account}"

alert_email="${ALERT_EMAIL:-}"
if [ -z "$alert_email" ]; then
  alert_email="$(cd "$repo_root" && gh variable get ALERT_EMAIL 2>/dev/null || true)"
fi
if [ -z "$alert_email" ]; then
  echo "Set ALERT_EMAIL, or the ALERT_EMAIL repository variable (docs/runbook.md, Alert email)." >&2
  exit 1
fi

work="$(mktemp -d "${TMPDIR:-/tmp}/mtd-bootstrap.XXXXXX")"
cleanup() {
  git -C "$repo_root" worktree remove --force "$work" >/dev/null 2>&1 || true
  git -C "$repo_root" worktree prune
  rmdir "$work" 2>/dev/null || true
}
trap cleanup EXIT

git -C "$repo_root" worktree add --detach --quiet "$work" "$ref"
echo "bootstrap $action at $ref ($(git -C "$work" rev-parse --short HEAD)), profile $profile, account $account"

tf_dir="$work/infra/bootstrap"
export AWS_PROFILE="$profile"
export TF_VAR_alert_email="$alert_email"
export TF_PLUGIN_CACHE_DIR="${TF_PLUGIN_CACHE_DIR:-$HOME/.terraform.d/plugin-cache}"
mkdir -p "$TF_PLUGIN_CACHE_DIR"

tf() { terraform -chdir="$tf_dir" "$@"; }

if [ "$action" = "migrate-state" ]; then
  local_state="${LOCAL_STATE:-$repo_root/infra/bootstrap/terraform.tfstate}"
  if [ ! -f "$local_state" ]; then
    echo "No local state at $local_state (set LOCAL_STATE)." >&2
    exit 1
  fi
  # Never replace a newer remote state with an older local one.
  local_serial="$(jq -r .serial "$local_state")"
  remote_serial="$(aws s3 cp "s3://$bucket/$state_key" - 2>/dev/null | jq -r .serial || true)"
  echo "local serial $local_serial, remote serial ${remote_serial:-none}"
  if [ -n "$remote_serial" ] && [ "$remote_serial" -gt "$local_serial" ]; then
    echo "The state in s3://$bucket/$state_key is newer than $local_state. Stopping." >&2
    exit 1
  fi
  cp "$local_state" "$tf_dir/terraform.tfstate"
  tf init -input=false -migrate-state -force-copy -backend-config="bucket=$bucket"
  echo "State is now in s3://$bucket/$state_key. Next: make bootstrap-plan."
  exit 0
fi

tf init -input=false -backend-config="bucket=$bucket"

if [ "$action" = "plan" ]; then
  tf plan -input=false
  exit 0
fi

tf plan -input=false -out=bootstrap.tfplan
read -r -p "Apply this plan to account $account? Type yes: " answer
if [ "$answer" != "yes" ]; then
  echo "Not applied."
  exit 1
fi
tf apply -input=false bootstrap.tfplan

# Outputs are identifiers, not secrets. Setting them here keeps CI in step
# with the roles without copying values by hand.
set_var() {
  (cd "$repo_root" && gh variable set "$1" --body "$2")
  echo "set $1"
}
set_var TF_STATE_BUCKET "$(tf output -raw state_bucket)"
set_var AWS_PLAN_ROLE_ARN "$(tf output -raw plan_role_arn)"
set_var AWS_ROLE_ARN "$(tf output -raw apply_role_arn)"
set_var AWS_TRANSFORM_ROLE_ARN "$(tf output -raw transform_role_arn)"
set_var AWS_OBSERVER_ROLE_ARN "$(tf output -raw observer_role_arn)"
set_var AWS_PUBLISH_ROLE_ARN "$(tf output -raw publish_role_arn)"
# The plan role can now read the bootstrap state, so bootstrap-plan.yml may run.
set_var BOOTSTRAP_PLAN_ENABLED true
