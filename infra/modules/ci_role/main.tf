# GitHub Actions roles for this repository, assumed through OIDC.
#
# - plan role: pull request jobs only. Read-only; plans run with -lock=false.
# - apply role: jobs running on the main branch only. Merging a pull request
#   is the approval (ADR-005); a pull request job, even one that rewrites the
#   workflow, cannot get these credentials because AWS checks the job's
#   branch, not the workflow file.
# - transform role: the scheduled dbt build on the main branch. Athena, Glue
#   and S3 data access for the prod target only; no infrastructure rights.
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
    buckets     = "arn:aws:s3:::${local.p}-*"
    raw_objects = "arn:aws:s3:::${local.p}-raw-*/*"
    objects     = "arn:aws:s3:::${local.p}-*/*"
    functions   = "arn:aws:lambda:${local.region}:${local.account_id}:function:${local.p}-*"
    roles       = "arn:aws:iam::${local.account_id}:role/${local.p}-*"
    log_groups  = "arn:aws:logs:${local.region}:${local.account_id}:log-group:/aws/lambda/${local.p}-*"
    log_streams = "arn:aws:logs:${local.region}:${local.account_id}:log-group:/aws/lambda/${local.p}-*:*"
    schedules   = "arn:aws:scheduler:${local.region}:${local.account_id}:schedule/*/${local.p}-*"
    sched_group = "arn:aws:scheduler:${local.region}:${local.account_id}:schedule-group/${local.p}-*"
    alarms      = "arn:aws:cloudwatch:${local.region}:${local.account_id}:alarm:${local.p}-*"
    topics      = "arn:aws:sns:${local.region}:${local.account_id}:${local.p}-*"
    workgroups  = "arn:aws:athena:${local.region}:${local.account_id}:workgroup/${local.p}-*"
    glue_cat    = "arn:aws:glue:${local.region}:${local.account_id}:catalog"
    glue_dbs    = "arn:aws:glue:${local.region}:${local.account_id}:database/${local.glue_p}_*"
    glue_tables = "arn:aws:glue:${local.region}:${local.account_id}:table/${local.glue_p}_*/*"
    parameters  = "arn:aws:ssm:${local.region}:${local.account_id}:parameter/${local.p}/*"
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

  # What the scheduled dbt run touches. Names are fixed by infra/envs/prod;
  # built here because the bootstrap cannot read that stack's outputs.
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
}

# ---- Trust ------------------------------------------------------------------

data "aws_iam_policy_document" "trust_plan" {
  statement {
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
      values   = ["${var.github_subject_prefix}:pull_request"]
    }
  }
}

data "aws_iam_policy_document" "trust_apply" {
  statement {
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
    # Only jobs on the apply branch. A job that names a GitHub environment
    # gets an environment subject instead and is refused, so no workflow can
    # route around this by adding one.
    condition {
      test     = "StringEquals"
      variable = "${local.oidc_host}:sub"
      values   = ["${var.github_subject_prefix}:ref:refs/heads/${var.apply_branch}"]
    }
  }
}

# Same subject as the apply role (jobs on main), but a separate role so the
# scheduled transform never holds deploy credentials (ADR-005 invariant).
data "aws_iam_policy_document" "trust_transform" {
  statement {
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
      values   = ["repo:${var.github_repository}:ref:refs/heads/${var.transform_branch}"]
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
      test     = "StringEquals"
      variable = "s3:ResourceAccount"
      values   = [local.account_id]
    }
  }
  statement {
    sid       = "ProjectResourcesRead"
    actions   = ["lambda:Get*", "lambda:List*", "sns:Get*", "sns:List*", "athena:Get*", "athena:List*", "scheduler:Get*", "scheduler:List*", "iam:Get*", "iam:List*"]
    resources = [local.arn.functions, local.arn.topics, local.arn.workgroups, local.arn.schedules, local.arn.sched_group, local.arn.roles, aws_iam_policy.workload_boundary.arn]
  }
  statement {
    sid       = "ProjectGlueRead"
    actions   = local.glue_read
    resources = [local.arn.glue_cat, local.arn.glue_dbs, local.arn.glue_tables]
  }
  # Metadata-only calls that do not support resource-level scoping. Lambda's
  # ListFunctions is left out on purpose: it returns every function's
  # environment variables across the account.
  statement {
    sid = "UnscopedDescribe"
    actions = [
      "cloudwatch:DescribeAlarms",
      "cloudwatch:ListTagsForResource",
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
      test     = "StringEquals"
      variable = "s3:ResourceAccount"
      values   = [local.account_id]
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
  statement {
    sid       = "ProjectServices"
    actions   = ["lambda:*", "sns:*", "scheduler:*", "athena:*"]
    resources = [local.arn.functions, local.arn.topics, local.arn.schedules, local.arn.sched_group, local.arn.workgroups]
  }
  # Function URLs and resource policies could expose a workload role (which
  # can read the TfL key) to unauthenticated callers. Scheduler invokes
  # through its own role, so neither is needed.
  statement {
    sid       = "NoPublicFunctions"
    effect    = "Deny"
    actions   = ["lambda:CreateFunctionUrlConfig", "lambda:UpdateFunctionUrlConfig", "lambda:AddPermission"]
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
    actions   = ["cloudwatch:PutMetricAlarm", "cloudwatch:DeleteAlarms", "cloudwatch:TagResource", "cloudwatch:UntagResource"]
    resources = [local.arn.alarms]
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
    sid = "AthenaWorkgroup"
    actions = [
      "athena:StartQueryExecution",
      "athena:GetQueryExecution",
      "athena:GetQueryResults",
      "athena:StopQueryExecution",
      "athena:GetWorkGroup",
    ]
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
      test     = "StringEquals"
      variable = "s3:ResourceAccount"
      values   = [local.account_id]
    }
  }
  statement {
    sid       = "RawRead"
    actions   = ["s3:GetObject"]
    resources = ["${local.transform.raw_bucket}/raw/*"]
    condition {
      test     = "StringEquals"
      variable = "s3:ResourceAccount"
      values   = [local.account_id]
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
      test     = "StringEquals"
      variable = "s3:ResourceAccount"
      values   = [local.account_id]
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
      test     = "StringEquals"
      variable = "s3:ResourceAccount"
      values   = [local.account_id]
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
  description          = "GitHub Actions, pull requests: Terraform plans (read-only)."
  assume_role_policy   = data.aws_iam_policy_document.trust_plan.json
  max_session_duration = 3600
}

resource "aws_iam_role_policy" "plan_read" {
  name   = "read"
  role   = aws_iam_role.plan.id
  policy = data.aws_iam_policy_document.read.json
}

resource "aws_iam_role" "apply" {
  name                 = "${var.ci_name_prefix}-ci-apply"
  description          = "GitHub Actions, main branch: Terraform deploys after merge."
  assume_role_policy   = data.aws_iam_policy_document.trust_apply.json
  max_session_duration = 3600
}

resource "aws_iam_role_policy" "apply_read" {
  name   = "read"
  role   = aws_iam_role.apply.id
  policy = data.aws_iam_policy_document.read.json
}

resource "aws_iam_role_policy" "apply_write" {
  name   = "write"
  role   = aws_iam_role.apply.id
  policy = data.aws_iam_policy_document.write.json
}

resource "aws_iam_role" "transform" {
  name                 = "${var.ci_name_prefix}-ci-transform"
  description          = "GitHub Actions, main branch: scheduled dbt build against the ${var.transform_target} target."
  assume_role_policy   = data.aws_iam_policy_document.trust_transform.json
  max_session_duration = 3600
}

resource "aws_iam_role_policy" "transform" {
  name   = "transform"
  role   = aws_iam_role.transform.id
  policy = data.aws_iam_policy_document.transform.json
}
