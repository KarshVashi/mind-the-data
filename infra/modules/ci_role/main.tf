# GitHub Actions roles for this repository, assumed through OIDC.
#
# - plan role: pull request plans (infra.yml, bootstrap-plan.yml), and the
#   weekly bootstrap drift check on main. Read-only; plans run with
#   -lock=false.
# - apply role: deploys from infra.yml on the main branch. Merging a pull
#   request is the approval (ADR-005).
# - transform role: the scheduled dbt build (transform-daily.yml). Athena,
#   Glue and S3 data access for the prod target only.
# - observer role: scheduled health checks (ops-daily.yml, ops-daytime.yml).
#   Read-only, plus queries in the ops workgroup.
# - publish role: the weekly export (publish-weekly.yml, M5). Reads the prod
#   marts through Athena; the upload to Cloudflare R2 uses GitHub secrets.
#
# AWS checks the job's branch (sub) and, per ADR-010, the workflow file
# (job_workflow_ref), so a pull request cannot deploy and a new workflow on
# main cannot borrow another workflow's role.
#
# The roles and the boundary policy are named outside the project prefix and
# outside the PassRole set, so nothing the pipeline manages can change its own
# access. They are applied by a human from infra/bootstrap, never by CI.

data "aws_caller_identity" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  region     = var.aws_region
  p          = var.resource_prefix
  glue_p     = replace(var.resource_prefix, "-", "_")
  oidc_host  = "token.actions.githubusercontent.com"

  arn = {
    buckets        = "arn:aws:s3:::${local.p}-*"
    raw_objects    = "arn:aws:s3:::${local.p}-raw-*/*"
    objects        = "arn:aws:s3:::${local.p}-*/*"
    functions      = "arn:aws:lambda:${local.region}:${local.account_id}:function:${local.p}-*"
    layers         = "arn:aws:lambda:${local.region}:${local.account_id}:layer:${local.p}-*"
    layer_versions = "arn:aws:lambda:${local.region}:${local.account_id}:layer:${local.p}-*:*"
    roles          = "arn:aws:iam::${local.account_id}:role/${local.p}-*"
    log_groups     = "arn:aws:logs:${local.region}:${local.account_id}:log-group:/aws/lambda/${local.p}-*"
    log_streams    = "arn:aws:logs:${local.region}:${local.account_id}:log-group:/aws/lambda/${local.p}-*:*"
    schedules      = "arn:aws:scheduler:${local.region}:${local.account_id}:schedule/*/${local.p}-*"
    sched_group    = "arn:aws:scheduler:${local.region}:${local.account_id}:schedule-group/${local.p}-*"
    alarms         = "arn:aws:cloudwatch:${local.region}:${local.account_id}:alarm:${local.p}-*"
    # Dashboards are global: their ARNs carry no region.
    dashboards  = "arn:aws:cloudwatch::${local.account_id}:dashboard/${local.p}-*"
    topics      = "arn:aws:sns:${local.region}:${local.account_id}:${local.p}-*"
    workgroups  = "arn:aws:athena:${local.region}:${local.account_id}:workgroup/${local.p}-*"
    glue_cat    = "arn:aws:glue:${local.region}:${local.account_id}:catalog"
    glue_dbs    = "arn:aws:glue:${local.region}:${local.account_id}:database/${local.glue_p}_*"
    glue_tables = "arn:aws:glue:${local.region}:${local.account_id}:table/${local.glue_p}_*/*"
    parameters  = "arn:aws:ssm:${local.region}:${local.account_id}:parameter/${local.p}/*"
    # Plain configuration values (M3 onwards). Kept apart from the TfL key
    # (/<prefix>/tfl/...) so CI can read and manage these without the key.
    config_path   = "arn:aws:ssm:${local.region}:${local.account_id}:parameter/${local.p}/config"
    config_params = "arn:aws:ssm:${local.region}:${local.account_id}:parameter/${local.p}/config/*"
  }

  # Glue actions authorise against the catalog ARN as well as the database or
  # table, so they are listed explicitly; a wildcard on the catalog would also
  # allow account-wide settings such as the catalog resource policy.
  glue_read = ["glue:GetDatabase", "glue:GetDatabases", "glue:GetTable", "glue:GetTables", "glue:GetTableVersion", "glue:GetTableVersions", "glue:GetPartition", "glue:GetPartitions", "glue:GetPartitionIndexes", "glue:GetTags"]
  glue_write = [
    "glue:CreateDatabase", "glue:UpdateDatabase", "glue:DeleteDatabase",
    "glue:CreateTable", "glue:UpdateTable", "glue:DeleteTable", "glue:DeleteTableVersion", "glue:BatchDeleteTableVersion",
    "glue:CreatePartition", "glue:BatchCreatePartition", "glue:UpdatePartition", "glue:DeletePartition", "glue:BatchDeletePartition",
    "glue:CreatePartitionIndex", "glue:DeletePartitionIndex",
    "glue:TagResource", "glue:UntagResource",
  ]

  # Athena query lifecycle in one workgroup, for the data roles.
  athena_query = [
    "athena:StartQueryExecution",
    "athena:GetQueryExecution",
    "athena:GetQueryResults",
    "athena:StopQueryExecution",
    "athena:GetWorkGroup",
  ]

  # What the data roles touch. Names are fixed by infra/envs/prod; built here
  # because the bootstrap cannot read that stack's outputs.
  transform = {
    workgroup      = "arn:aws:athena:${local.region}:${local.account_id}:workgroup/${local.p}-${var.transform_target}"
    raw_bucket     = "arn:aws:s3:::${local.p}-raw-${local.account_id}"
    curated_bucket = "arn:aws:s3:::${local.p}-curated-${local.account_id}"
    results_bucket = "arn:aws:s3:::${local.p}-athena-results-${local.account_id}"
    raw_db         = "arn:aws:glue:${local.region}:${local.account_id}:database/${local.glue_p}_raw"
    raw_tables     = "arn:aws:glue:${local.region}:${local.account_id}:table/${local.glue_p}_raw/*"
    default_db     = "arn:aws:glue:${local.region}:${local.account_id}:database/default"
    target_db      = "arn:aws:glue:${local.region}:${local.account_id}:database/${local.glue_p}_${var.transform_target}"
    target_tables  = "arn:aws:glue:${local.region}:${local.account_id}:table/${local.glue_p}_${var.transform_target}/*"
  }

  # The observer's own workgroup (<prefix>-<observer_workgroup>, created by
  # infra/envs/prod with a small scan cap) and its results prefix.
  observer_workgroup = "arn:aws:athena:${local.region}:${local.account_id}:workgroup/${local.p}-${var.observer_workgroup}"

  s3_this_account = {
    test     = "StringEquals"
    variable = "s3:ResourceAccount"
    values   = [local.account_id]
  }
}

# ---- Trust ------------------------------------------------------------------

# Each role trusts a list of (subject, workflow files) pairs. The subject
# fixes the repository by id (ADR-009) and the event context; the
# job_workflow_ref claim, which AWS exposes as its own condition key, fixes
# the workflow file (ADR-010). The repository's OIDC subject template stays
# the default.
#
# Pull request runs carry refs/pull/<n>/merge in job_workflow_ref, hence the
# wildcard there; every other ref is matched exactly.
locals {
  # repo:Owner@id/name@id -> Owner/name, the form job_workflow_ref uses.
  repository_path = replace(replace(var.github_subject_prefix, "/^repo:/", ""), "/@[0-9]+/", "")
  workflows_path  = "${local.repository_path}/.github/workflows"

  # Workflow file names are the contract with .github/workflows: renaming
  # one needs a bootstrap re-apply.
  workflow_files = {
    plan_pull_request = ["infra.yml", "bootstrap-plan.yml"]
    plan_main         = ["bootstrap-plan.yml"]
    apply             = ["infra.yml"]
    transform         = ["transform-daily.yml"]
    observer          = ["ops-daily.yml", "ops-daytime.yml"]
    publish           = ["publish-weekly.yml"]
  }

  main_sub = {
    apply     = "${var.github_subject_prefix}:ref:refs/heads/${var.apply_branch}"
    transform = "${var.github_subject_prefix}:ref:refs/heads/${var.transform_branch}"
    scheduled = "${var.github_subject_prefix}:ref:refs/heads/${var.workflow_branch}"
  }

  # Roles that existed before ADR-010 (plan on pull requests, apply,
  # transform) are bound to their workflows once enforce_workflow_binding is
  # true. New trust (observer, publish, the plan role on main) is bound from
  # the start: if the claim did not match, only those new paths would fail.
  trust = {
    plan = [
      {
        sub       = "${var.github_subject_prefix}:pull_request"
        test      = "StringLike"
        workflows = var.enforce_workflow_binding ? [for f in local.workflow_files.plan_pull_request : "${local.workflows_path}/${f}@refs/pull/*/merge"] : []
      },
      {
        sub       = local.main_sub.scheduled
        test      = "StringEquals"
        workflows = [for f in local.workflow_files.plan_main : "${local.workflows_path}/${f}@refs/heads/${var.workflow_branch}"]
      },
    ]
    # A job that names a GitHub environment gets an environment subject and
    # is refused, so no workflow can route around the branch rule with one.
    apply = [{
      sub       = local.main_sub.apply
      test      = "StringEquals"
      workflows = var.enforce_workflow_binding ? [for f in local.workflow_files.apply : "${local.workflows_path}/${f}@refs/heads/${var.apply_branch}"] : []
    }]
    transform = [{
      sub       = local.main_sub.transform
      test      = "StringEquals"
      workflows = var.enforce_workflow_binding ? [for f in local.workflow_files.transform : "${local.workflows_path}/${f}@refs/heads/${var.transform_branch}"] : []
    }]
    observer = [{
      sub       = local.main_sub.scheduled
      test      = "StringEquals"
      workflows = [for f in local.workflow_files.observer : "${local.workflows_path}/${f}@refs/heads/${var.workflow_branch}"]
    }]
    publish = [{
      sub       = local.main_sub.scheduled
      test      = "StringEquals"
      workflows = [for f in local.workflow_files.publish : "${local.workflows_path}/${f}@refs/heads/${var.workflow_branch}"]
    }]
  }
}

data "aws_iam_policy_document" "trust" {
  for_each = local.trust

  dynamic "statement" {
    for_each = each.value
    content {
      actions = ["sts:AssumeRoleWithWebIdentity"]
      principals {
        type        = "Federated"
        identifiers = [var.oidc_provider_arn]
      }
      condition {
        test     = "StringEquals"
        variable = "${local.oidc_host}:aud"
        values   = ["sts.amazonaws.com"]
      }
      condition {
        test     = "StringEquals"
        variable = "${local.oidc_host}:sub"
        values   = [statement.value.sub]
      }
      dynamic "condition" {
        for_each = length(statement.value.workflows) > 0 ? [statement.value] : []
        content {
          test     = condition.value.test
          variable = "${local.oidc_host}:job_workflow_ref"
          values   = condition.value.workflows
        }
      }
    }
  }
}

# ---- Read access (plan, and refresh during apply) --------------------------

data "aws_iam_policy_document" "read" {
  statement {
    sid       = "StateRead"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = [var.state_bucket_arn]
  }
  statement {
    sid       = "StateObjectRead"
    actions   = ["s3:GetObject"]
    resources = ["${var.state_bucket_arn}/${var.state_key_prefix}*"]
  }
  # S3 ARNs carry no account, so pin reads to buckets in this account.
  statement {
    sid       = "ProjectBucketsRead"
    actions   = ["s3:Get*", "s3:List*"]
    resources = [local.arn.buckets, local.arn.objects]
    condition {
      test     = local.s3_this_account.test
      variable = local.s3_this_account.variable
      values   = local.s3_this_account.values
    }
  }
  statement {
    sid       = "ProjectResourcesRead"
    actions   = ["lambda:Get*", "lambda:List*", "sns:Get*", "sns:List*", "athena:Get*", "athena:List*", "scheduler:Get*", "scheduler:List*", "iam:Get*", "iam:List*"]
    resources = [local.arn.functions, local.arn.layers, local.arn.layer_versions, local.arn.topics, local.arn.workgroups, local.arn.schedules, local.arn.sched_group, local.arn.roles, aws_iam_policy.workload_boundary.arn]
  }
  statement {
    sid       = "ProjectGlueRead"
    actions   = local.glue_read
    resources = [local.arn.glue_cat, local.arn.glue_dbs, local.arn.glue_tables]
  }
  statement {
    sid       = "ProjectDashboardsRead"
    actions   = ["cloudwatch:GetDashboard"]
    resources = [local.arn.dashboards]
  }
  # Configuration parameters only, never the TfL key. A SecureString here
  # would also need kms:Decrypt, which these roles do not have.
  statement {
    sid       = "ConfigParametersRead"
    actions   = ["ssm:GetParameter", "ssm:GetParameters", "ssm:ListTagsForResource"]
    resources = [local.arn.config_params]
  }
  # Metadata-only calls that do not support resource-level scoping. Lambda's
  # ListFunctions is left out on purpose: it returns every function's
  # environment variables across the account.
  statement {
    sid = "UnscopedDescribe"
    actions = [
      "cloudwatch:DescribeAlarms",
      "cloudwatch:ListDashboards",
      "cloudwatch:ListTagsForResource",
      "lambda:ListLayers",
      "lambda:ListLayerVersions",
      "logs:DescribeLogGroups",
      "logs:ListTagsForResource",
      "scheduler:ListScheduleGroups",
      "scheduler:ListSchedules",
      "sns:ListTopics",
      "ssm:DescribeParameters",
    ]
    resources = ["*"]
  }
}

# ---- Bootstrap read (plan role only: bootstrap-plan.yml) -------------------

# Enough to refresh infra/bootstrap's own state: its state object, the state
# bucket's settings, the OIDC provider, the CI roles and boundary, the
# budget, the cost allocation tag and the Lambda quota. The apply role does
# not get this; CI never applies the bootstrap.
data "aws_iam_policy_document" "bootstrap_read" {
  statement {
    sid       = "BootstrapStateRead"
    actions   = ["s3:GetObject"]
    resources = ["${var.state_bucket_arn}/${var.bootstrap_state_key}"]
  }
  # The four named actions are needed alongside s3:GetBucket*: their IAM
  # names do not start with GetBucket, though the provider reads them.
  statement {
    sid = "StateBucketSettingsRead"
    actions = [
      "s3:GetBucket*",
      "s3:GetLifecycleConfiguration",
      "s3:GetEncryptionConfiguration",
      "s3:GetReplicationConfiguration",
      "s3:GetAccelerateConfiguration",
    ]
    resources = [var.state_bucket_arn]
  }
  statement {
    sid = "CiRolesRead"
    actions = [
      "iam:GetRole",
      "iam:GetRolePolicy",
      "iam:ListRolePolicies",
      "iam:ListAttachedRolePolicies",
      "iam:ListRoleTags",
      "iam:ListInstanceProfilesForRole",
    ]
    resources = ["arn:aws:iam::${local.account_id}:role/${var.ci_name_prefix}-ci-*"]
  }
  statement {
    sid       = "OidcProviderRead"
    actions   = ["iam:GetOpenIDConnectProvider", "iam:ListOpenIDConnectProviderTags"]
    resources = [var.oidc_provider_arn]
  }
  statement {
    sid       = "BudgetRead"
    actions   = ["budgets:ViewBudget", "budgets:ListTagsForResource"]
    resources = ["arn:aws:budgets::${local.account_id}:budget/${local.p}-*"]
  }
  statement {
    sid = "UnscopedBootstrapDescribe"
    actions = [
      "iam:ListOpenIDConnectProviders",
      "ce:ListCostAllocationTags",
      "servicequotas:GetServiceQuota",
      "servicequotas:GetAWSDefaultServiceQuota",
      "servicequotas:GetRequestedServiceQuotaChange",
      "servicequotas:ListRequestedServiceQuotaChangeHistoryByQuota",
    ]
    resources = ["*"]
  }
}

# ---- Write access (apply only) ---------------------------------------------

data "aws_iam_policy_document" "write" {
  statement {
    sid       = "StateWrite"
    actions   = ["s3:PutObject", "s3:DeleteObject"]
    resources = ["${var.state_bucket_arn}/${var.state_key_prefix}*"]
  }
  # A bucket that does not exist yet has no owning account to compare, and a
  # new bucket always belongs to the caller's account.
  statement {
    sid       = "CreateProjectBuckets"
    actions   = ["s3:CreateBucket"]
    resources = [local.arn.buckets]
  }
  statement {
    sid       = "ProjectBuckets"
    actions   = ["s3:*"]
    resources = [local.arn.buckets, local.arn.objects]
    condition {
      test     = local.s3_this_account.test
      variable = local.s3_this_account.variable
      values   = local.s3_this_account.values
    }
  }
  # No merged change may delete a project bucket or object versions, change
  # versioning, or set ACLs. Bucket public access blocks cannot be protected
  # here (removing one is authorised as s3:PutBucketPublicAccessBlock, which
  # creating one also needs); the account-level block is the control.
  statement {
    sid    = "ProtectProjectBuckets"
    effect = "Deny"
    actions = [
      "s3:DeleteBucket",
      "s3:DeleteObjectVersion",
      "s3:PutBucketVersioning",
      "s3:DeleteBucketPolicy",
      "s3:PutBucketAcl",
      "s3:PutObjectAcl",
    ]
    resources = [local.arn.buckets, local.arn.objects]
  }
  # Raw is unversioned and irreplaceable, so CI may never delete raw objects.
  statement {
    sid       = "ProtectRawObjects"
    effect    = "Deny"
    actions   = ["s3:DeleteObject"]
    resources = [local.arn.raw_objects]
  }
  # lambda:* on project functions includes lambda:InvokeFunction, which the
  # deploy job uses for its post-apply smoke test. Layers (M3 onwards) are
  # project-named too.
  statement {
    sid       = "ProjectServices"
    actions   = ["lambda:*", "sns:*", "scheduler:*", "athena:*"]
    resources = [local.arn.functions, local.arn.layers, local.arn.layer_versions, local.arn.topics, local.arn.schedules, local.arn.sched_group, local.arn.workgroups]
  }
  # Function URLs and resource policies could expose a workload role (which
  # can read the TfL key) to unauthenticated callers. Scheduler invokes
  # through its own role, so neither is needed. Layer permissions would share
  # a layer with other accounts.
  statement {
    sid       = "NoPublicFunctions"
    effect    = "Deny"
    actions   = ["lambda:CreateFunctionUrlConfig", "lambda:UpdateFunctionUrlConfig", "lambda:AddPermission", "lambda:AddLayerVersionPermission"]
    resources = ["*"]
  }
  statement {
    sid       = "ProjectGlue"
    actions   = concat(local.glue_read, local.glue_write)
    resources = [local.arn.glue_cat, local.arn.glue_dbs, local.arn.glue_tables]
  }
  statement {
    sid       = "ProjectLogs"
    actions   = ["logs:*"]
    resources = [local.arn.log_groups, local.arn.log_streams]
  }
  statement {
    sid       = "ProjectAlarms"
    actions   = ["cloudwatch:PutMetricAlarm", "cloudwatch:PutCompositeAlarm", "cloudwatch:DeleteAlarms", "cloudwatch:TagResource", "cloudwatch:UntagResource"]
    resources = [local.arn.alarms]
  }
  statement {
    sid       = "ProjectDashboards"
    actions   = ["cloudwatch:PutDashboard", "cloudwatch:DeleteDashboards"]
    resources = [local.arn.dashboards]
  }
  # Workload roles may be created and given policies only while they carry
  # the project boundary, so a role made by CI can never exceed it.
  statement {
    sid = "WorkloadRolesWithBoundary"
    actions = [
      "iam:CreateRole",
      "iam:PutRolePermissionsBoundary",
      "iam:PutRolePolicy",
      "iam:DeleteRolePolicy",
      "iam:AttachRolePolicy",
      "iam:DetachRolePolicy",
    ]
    resources = [local.arn.roles]
    condition {
      test     = "StringEquals"
      variable = "iam:PermissionsBoundary"
      values   = [aws_iam_policy.workload_boundary.arn]
    }
  }
  # iam:PermissionsBoundary is not evaluated for these actions. This relies
  # on every mind-the-data-* role carrying the boundary (ADR-005).
  statement {
    sid = "WorkloadRolesManage"
    actions = [
      "iam:DeleteRole",
      "iam:TagRole",
      "iam:UntagRole",
      "iam:UpdateRole",
      "iam:UpdateRoleDescription",
      "iam:UpdateAssumeRolePolicy",
    ]
    resources = [local.arn.roles]
  }
  statement {
    sid       = "PassWorkloadRoles"
    actions   = ["iam:PassRole"]
    resources = [local.arn.roles]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["lambda.amazonaws.com", "scheduler.amazonaws.com"]
    }
  }
  statement {
    sid       = "ParameterTags"
    actions   = ["ssm:ListTagsForResource"]
    resources = [local.arn.parameters]
  }
  # Configuration values under /<prefix>/config/ only. The TfL key lives
  # outside this path and stays out of Terraform (ADR-005).
  statement {
    sid = "ConfigParameters"
    actions = [
      "ssm:PutParameter",
      "ssm:DeleteParameter",
      "ssm:DeleteParameters",
      "ssm:AddTagsToResource",
      "ssm:RemoveTagsFromResource",
    ]
    resources = [local.arn.config_params]
  }
  statement {
    sid       = "BoundaryIsFixed"
    effect    = "Deny"
    actions   = ["iam:DeleteRolePermissionsBoundary", "iam:CreatePolicyVersion", "iam:SetDefaultPolicyVersion", "iam:DeletePolicy"]
    resources = ["*"]
  }
}

# ---- Transform access (scheduled dbt build) ---------------------------------

# dbt on Athena: queries in one workgroup, reads raw, and owns one Glue
# database and its prefixes in the curated and results buckets. It cannot
# change raw, the other target, or any infrastructure.
data "aws_iam_policy_document" "transform" {
  statement {
    sid       = "AthenaWorkgroup"
    actions   = local.athena_query
    resources = [local.transform.workgroup]
  }
  # Athena looks up the `default` database when it runs queries and DDL,
  # so it is readable (never writable) as well.
  statement {
    sid       = "GlueRead"
    actions   = local.glue_read
    resources = [local.arn.glue_cat, local.transform.default_db, local.transform.raw_db, local.transform.raw_tables, local.transform.target_db, local.transform.target_tables]
  }
  # dbt creates its database with `create schema if not exists`, swaps and
  # drops tables, and prunes old table versions through the Glue API. No
  # DeleteDatabase: dbt build never drops a schema, and a stray macro must
  # not be able to empty the whole prod database.
  statement {
    sid = "GlueTargetWrite"
    actions = [
      "glue:CreateDatabase", "glue:UpdateDatabase",
      "glue:CreateTable", "glue:UpdateTable", "glue:DeleteTable",
      "glue:DeleteTableVersion", "glue:BatchDeleteTableVersion",
      "glue:CreatePartition", "glue:BatchCreatePartition", "glue:UpdatePartition", "glue:BatchUpdatePartition",
      "glue:DeletePartition", "glue:BatchDeletePartition",
    ]
    resources = [local.arn.glue_cat, local.transform.target_db, local.transform.target_tables]
  }
  statement {
    sid       = "Buckets"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation", "s3:ListBucketMultipartUploads"]
    resources = [local.transform.raw_bucket, local.transform.curated_bucket, local.transform.results_bucket]
    condition {
      test     = local.s3_this_account.test
      variable = local.s3_this_account.variable
      values   = local.s3_this_account.values
    }
  }
  statement {
    sid       = "RawRead"
    actions   = ["s3:GetObject"]
    resources = ["${local.transform.raw_bucket}/raw/*"]
    condition {
      test     = local.s3_this_account.test
      variable = local.s3_this_account.variable
      values   = local.s3_this_account.values
    }
  }
  # Iceberg data and Athena results for the target only. Athena writes and
  # cleans up with the caller's credentials.
  statement {
    sid = "TargetObjects"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:AbortMultipartUpload",
      "s3:ListMultipartUploadParts",
    ]
    resources = [
      "${local.transform.curated_bucket}/${var.transform_target}/*",
      "${local.transform.results_bucket}/${var.transform_target}/*",
    ]
    condition {
      test     = local.s3_this_account.test
      variable = local.s3_this_account.variable
      values   = local.s3_this_account.values
    }
  }
}

# ---- Observer access (scheduled health checks) ------------------------------

# Read-only: raw objects, alarms and metrics, Lambda configuration and logs,
# and the Glue catalog. Athena only in its own workgroup, whose small scan
# cap bounds what a check can cost. No SSM: the checks never need the key.
data "aws_iam_policy_document" "observer" {
  statement {
    sid       = "AthenaOpsWorkgroup"
    actions   = local.athena_query
    resources = [local.observer_workgroup]
  }
  statement {
    sid       = "GlueRead"
    actions   = local.glue_read
    resources = [local.arn.glue_cat, local.transform.default_db, local.transform.raw_db, local.transform.raw_tables, local.transform.target_db, local.transform.target_tables]
  }
  statement {
    sid       = "Buckets"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = [local.transform.raw_bucket, local.transform.curated_bucket, local.transform.results_bucket]
    condition {
      test     = local.s3_this_account.test
      variable = local.s3_this_account.variable
      values   = local.s3_this_account.values
    }
  }
  # Raw objects, and the prod marts the ops queries may read through Athena.
  statement {
    sid       = "DataRead"
    actions   = ["s3:GetObject"]
    resources = ["${local.transform.raw_bucket}/raw/*", "${local.transform.curated_bucket}/${var.transform_target}/*"]
    condition {
      test     = local.s3_this_account.test
      variable = local.s3_this_account.variable
      values   = local.s3_this_account.values
    }
  }
  # Athena writes query results with the caller's credentials.
  statement {
    sid = "OpsResults"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:AbortMultipartUpload",
      "s3:ListMultipartUploadParts",
    ]
    resources = ["${local.transform.results_bucket}/${var.observer_workgroup}/*"]
    condition {
      test     = local.s3_this_account.test
      variable = local.s3_this_account.variable
      values   = local.s3_this_account.values
    }
  }
  statement {
    sid       = "FunctionConfigRead"
    actions   = ["lambda:GetFunctionConfiguration", "lambda:GetFunctionConcurrency", "lambda:GetFunctionEventInvokeConfig"]
    resources = [local.arn.functions]
  }
  statement {
    sid = "LogsRead"
    actions = [
      "logs:DescribeLogStreams",
      "logs:FilterLogEvents",
      "logs:GetLogEvents",
      "logs:StartQuery",
    ]
    resources = [local.arn.log_groups, local.arn.log_streams]
  }
  # Metric and alarm reads, and Logs Insights results, have no resource-level
  # scoping. They return metadata and numbers, never payloads.
  statement {
    sid = "UnscopedRead"
    actions = [
      "cloudwatch:DescribeAlarms",
      "cloudwatch:DescribeAlarmHistory",
      "cloudwatch:DescribeAlarmsForMetric",
      "cloudwatch:GetMetricData",
      "cloudwatch:GetMetricStatistics",
      "cloudwatch:ListMetrics",
      "lambda:GetAccountSettings",
      "logs:DescribeLogGroups",
      "logs:GetQueryResults",
      "logs:StopQuery",
    ]
    resources = ["*"]
  }
}

# ---- Publish access (weekly export, M5) -------------------------------------

# Reads the prod marts through the prod workgroup and writes only Athena
# results. Publishing itself goes to Cloudflare R2 with GitHub secrets, so
# no AWS write beyond query results is needed.
data "aws_iam_policy_document" "publish" {
  statement {
    sid       = "AthenaWorkgroup"
    actions   = local.athena_query
    resources = [local.transform.workgroup]
  }
  statement {
    sid       = "GlueRead"
    actions   = local.glue_read
    resources = [local.arn.glue_cat, local.transform.default_db, local.transform.target_db, local.transform.target_tables]
  }
  statement {
    sid       = "Buckets"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = [local.transform.curated_bucket, local.transform.results_bucket]
    condition {
      test     = local.s3_this_account.test
      variable = local.s3_this_account.variable
      values   = local.s3_this_account.values
    }
  }
  statement {
    sid       = "MartsRead"
    actions   = ["s3:GetObject"]
    resources = ["${local.transform.curated_bucket}/${var.transform_target}/*"]
    condition {
      test     = local.s3_this_account.test
      variable = local.s3_this_account.variable
      values   = local.s3_this_account.values
    }
  }
  # Query results, including UNLOAD output for the monthly open dataset (M7).
  statement {
    sid = "Results"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:AbortMultipartUpload",
      "s3:ListMultipartUploadParts",
    ]
    resources = ["${local.transform.results_bucket}/${var.transform_target}/*"]
    condition {
      test     = local.s3_this_account.test
      variable = local.s3_this_account.variable
      values   = local.s3_this_account.values
    }
  }
}

# ---- Boundary for every role the pipeline creates -------------------------

data "aws_iam_policy_document" "workload_boundary" {
  statement {
    sid       = "ProjectData"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:ListBucket", "s3:GetBucketLocation"]
    resources = [local.arn.buckets, local.arn.objects]
    condition {
      test     = local.s3_this_account.test
      variable = local.s3_this_account.variable
      values   = local.s3_this_account.values
    }
  }
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = [local.arn.log_groups, local.arn.log_streams]
  }
  statement {
    sid       = "Metrics"
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "cloudwatch:namespace"
      values   = [var.metric_namespace]
    }
  }
  statement {
    sid       = "AppKey"
    actions   = ["ssm:GetParameter", "ssm:GetParameters"]
    resources = [local.arn.parameters]
  }
  # Lambdas may load their configuration values as one path (M3 onwards).
  statement {
    sid       = "ConfigByPath"
    actions   = ["ssm:GetParametersByPath"]
    resources = [local.arn.config_path, local.arn.config_params]
  }
  statement {
    sid       = "AppKeyDecrypt"
    actions   = ["kms:Decrypt"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["ssm.${local.region}.amazonaws.com"]
    }
  }
  statement {
    sid       = "Notify"
    actions   = ["sns:Publish"]
    resources = [local.arn.topics]
  }
  statement {
    sid       = "InvokeProjectFunctions"
    actions   = ["lambda:InvokeFunction"]
    resources = [local.arn.functions]
  }
}

resource "aws_iam_policy" "workload_boundary" {
  name        = "${var.ci_name_prefix}-workload-boundary"
  description = "Upper limit for every IAM role created by the ${local.p} pipeline."
  policy      = data.aws_iam_policy_document.workload_boundary.json
}

# ---- Roles ------------------------------------------------------------------

resource "aws_iam_role" "plan" {
  name                 = "${var.ci_name_prefix}-ci-plan"
  description          = "GitHub Actions: Terraform plans on pull requests and the bootstrap drift check (read-only)."
  assume_role_policy   = data.aws_iam_policy_document.trust["plan"].json
  max_session_duration = 3600
}

resource "aws_iam_role_policy" "plan_read" {
  name   = "read"
  role   = aws_iam_role.plan.id
  policy = data.aws_iam_policy_document.read.json
}

resource "aws_iam_role_policy" "plan_bootstrap_read" {
  name   = "bootstrap-read"
  role   = aws_iam_role.plan.id
  policy = data.aws_iam_policy_document.bootstrap_read.json
}

resource "aws_iam_role" "apply" {
  name                 = "${var.ci_name_prefix}-ci-apply"
  description          = "GitHub Actions, main branch: Terraform deploys after merge."
  assume_role_policy   = data.aws_iam_policy_document.trust["apply"].json
  max_session_duration = 3600
}

resource "aws_iam_role_policy" "apply_read" {
  name   = "read"
  role   = aws_iam_role.apply.id
  policy = data.aws_iam_policy_document.read.json
}

# IAM limits a role's inline policies to 10,240 characters in total
# (whitespace excluded). read + write is an estimated 7,400 after this change; check
# the total when adding to either, or split write into a managed policy.
resource "aws_iam_role_policy" "apply_write" {
  name   = "write"
  role   = aws_iam_role.apply.id
  policy = data.aws_iam_policy_document.write.json
}

resource "aws_iam_role" "transform" {
  name                 = "${var.ci_name_prefix}-ci-transform"
  description          = "GitHub Actions, main branch: scheduled dbt build against the ${var.transform_target} target."
  assume_role_policy   = data.aws_iam_policy_document.trust["transform"].json
  max_session_duration = 3600
}

resource "aws_iam_role_policy" "transform" {
  name   = "transform"
  role   = aws_iam_role.transform.id
  policy = data.aws_iam_policy_document.transform.json
}

resource "aws_iam_role" "observer" {
  name                 = "${var.ci_name_prefix}-ci-observer"
  description          = "GitHub Actions, main branch: scheduled health checks (read-only, ops workgroup)."
  assume_role_policy   = data.aws_iam_policy_document.trust["observer"].json
  max_session_duration = 3600
}

resource "aws_iam_role_policy" "observer" {
  name   = "observer"
  role   = aws_iam_role.observer.id
  policy = data.aws_iam_policy_document.observer.json
}

resource "aws_iam_role" "publish" {
  name                 = "${var.ci_name_prefix}-ci-publish"
  description          = "GitHub Actions, main branch: weekly export of the ${var.transform_target} marts."
  assume_role_policy   = data.aws_iam_policy_document.trust["publish"].json
  max_session_duration = 3600
}

resource "aws_iam_role_policy" "publish" {
  name   = "publish"
  role   = aws_iam_role.publish.id
  policy = data.aws_iam_policy_document.publish.json
}
