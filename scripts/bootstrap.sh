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

# Prints "present" or "absent"; any other failure (expired session, wrong
# profile, throttling) stops the script rather than looking like "absent".
remote_state() {
  local err
  if err="$(aws s3api head-object --profile "$profile" --bucket "$bucket" --key "$state_key" 2>&1 >/dev/null)"; then
    echo present
  elif printf '%s' "$err" | grep -qE '\((404|NoSuchKey|NotFound)\)|Not Found'; then
    echo absent
  else
    echo "Could not check s3://$bucket/$state_key: $err" >&2
    return 1
  fi
}
remote="$(remote_state)"

if [ "$action" != "migrate-state" ]; then
  # Without the state, a plan would propose rebuilding everything, and an
  # apply would half-create it into a second state.
  if [ "$remote" != "present" ]; then
    echo "No bootstrap state at s3://$bucket/$state_key. Run make bootstrap-migrate-state first (docs/runbook.md, H6)." >&2
    exit 1
  fi
  alert_email="${ALERT_EMAIL:-}"
  if [ -z "$alert_email" ]; then
    alert_email="$(cd "$repo_root" && gh variable get ALERT_EMAIL 2>/dev/null || true)"
  fi
  if [ -z "$alert_email" ]; then
    echo "Set ALERT_EMAIL, or the ALERT_EMAIL repository variable (docs/runbook.md, Alert email)." >&2
    exit 1
  fi
  export TF_VAR_alert_email="$alert_email"
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
export TF_PLUGIN_CACHE_DIR="${TF_PLUGIN_CACHE_DIR:-$HOME/.terraform.d/plugin-cache}"
mkdir -p "$TF_PLUGIN_CACHE_DIR"

tf() { terraform -chdir="$tf_dir" "$@"; }

if [ "$action" = "migrate-state" ]; then
  local_state="${LOCAL_STATE:-$repo_root/infra/bootstrap/terraform.tfstate}"
  if [ ! -f "$local_state" ]; then
    echo "No local state at $local_state (set LOCAL_STATE)." >&2
    exit 1
  fi
  # Overwrite the remote copy only if it is the same state (same lineage)
  # and not newer. Older copies stay as object versions in the bucket.
  local_serial="$(jq -er .serial "$local_state")"
  local_lineage="$(jq -er .lineage "$local_state")"
  if [ "$remote" = "present" ]; then
    remote_json="$(aws s3 cp --profile "$profile" "s3://$bucket/$state_key" -)"
    remote_serial="$(printf '%s' "$remote_json" | jq -er .serial)"
    remote_lineage="$(printf '%s' "$remote_json" | jq -er .lineage)"
    echo "local serial $local_serial, remote serial $remote_serial"
    if [ "$remote_lineage" != "$local_lineage" ]; then
      echo "s3://$bucket/$state_key is a different state (lineage $remote_lineage, local $local_lineage). Stopping." >&2
      exit 1
    fi
    if [ "$remote_serial" -gt "$local_serial" ]; then
      echo "s3://$bucket/$state_key is newer than $local_state. Stopping." >&2
      exit 1
    fi
  else
    echo "local serial $local_serial, no remote state yet"
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
# The workflows derive the remaining names from the account id.
set_var AWS_ACCOUNT_ID "$account"
set_var TF_STATE_BUCKET "$(tf output -raw state_bucket)"
set_var AWS_PLAN_ROLE_ARN "$(tf output -raw plan_role_arn)"
set_var AWS_ROLE_ARN "$(tf output -raw apply_role_arn)"
set_var AWS_TRANSFORM_ROLE_ARN "$(tf output -raw transform_role_arn)"
set_var AWS_OBSERVER_ROLE_ARN "$(tf output -raw observer_role_arn)"
set_var AWS_PUBLISH_ROLE_ARN "$(tf output -raw publish_role_arn)"
# The plan role can now read the bootstrap state, so bootstrap-plan.yml may run.
set_var BOOTSTRAP_PLAN_ENABLED true
