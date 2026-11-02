# GitHub Actions roles for this repository, assumed through OIDC.
#
# - plan role: pull requests; read-only, runs `terraform plan -lock=false`.
# - apply role: only the `production` environment (which is restricted to
#   main and needs a reviewer); runs `terraform apply`.
#
# Both roles, and the boundary policy, are named outside the project prefix,
# so nothing the pipeline is allowed to manage can change its own access.
# They are applied by a human from infra/bootstrap, never by CI.

data "aws_caller_identity" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  region     = var.aws_region
  p          = var.resource_prefix
  glue_p     = replace(var.resource_prefix, "-", "_")
  oidc_host  = "token.actions.githubusercontent.com"

  arn = {
    buckets     = "arn:aws:s3:::${local.p}-*"
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
      values   = ["repo:${var.github_repository}:pull_request"]
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
    # A job that names an environment gets this subject regardless of branch;
    # the environment's deployment-branch rule (main only) and required
    # reviewer provide the branch and approval checks.
    condition {
      test     = "StringEquals"
      variable = "${local.oidc_host}:sub"
      values   = ["repo:${var.github_repository}:environment:${var.apply_environment}"]
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
  statement {
    sid       = "ProjectBucketsRead"
    actions   = ["s3:Get*", "s3:List*"]
    resources = [local.arn.buckets, local.arn.objects]
  }
  statement {
    sid       = "ProjectResourcesRead"
    actions   = ["lambda:Get*", "lambda:List*", "sns:Get*", "sns:List*", "athena:Get*", "athena:List*", "glue:Get*", "scheduler:Get*", "scheduler:List*", "iam:Get*", "iam:List*"]
    resources = [local.arn.functions, local.arn.topics, local.arn.workgroups, local.arn.glue_cat, local.arn.glue_dbs, local.arn.glue_tables, local.arn.schedules, local.arn.sched_group, local.arn.roles, aws_iam_policy.workload_boundary.arn]
  }
  # These describe/list calls do not support resource-level scoping and
  # return metadata only.
  statement {
    sid = "UnscopedDescribe"
    actions = [
      "cloudwatch:DescribeAlarms",
      "cloudwatch:ListTagsForResource",
      "lambda:ListFunctions",
      "logs:DescribeLogGroups",
      "logs:ListTagsForResource",
      "scheduler:ListScheduleGroups",
      "scheduler:ListSchedules",
      "sns:ListTopics",
    ]
    resources = ["*"]
  }
  statement {
    sid       = "ParameterMetadata"
    actions   = ["ssm:DescribeParameters"]
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
  statement {
    sid       = "ProjectBuckets"
    actions   = ["s3:*"]
    resources = [local.arn.buckets, local.arn.objects]
  }
  statement {
    sid       = "ProjectServices"
    actions   = ["lambda:*", "sns:*", "scheduler:*", "athena:*", "glue:*"]
    resources = [local.arn.functions, local.arn.topics, local.arn.schedules, local.arn.sched_group, local.arn.workgroups, local.arn.glue_cat, local.arn.glue_dbs, local.arn.glue_tables]
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
    sid       = "NeverRemoveBoundary"
    effect    = "Deny"
    actions   = ["iam:DeleteRolePermissionsBoundary"]
    resources = ["*"]
  }
}

# ---- Boundary for every role the pipeline creates -------------------------

data "aws_iam_policy_document" "workload_boundary" {
  statement {
    sid       = "ProjectData"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:ListBucket", "s3:GetBucketLocation"]
    resources = [local.arn.buckets, local.arn.objects]
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
  description          = "GitHub Actions, pull requests: terraform plan (read-only)."
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
  description          = "GitHub Actions, production environment: terraform apply."
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
