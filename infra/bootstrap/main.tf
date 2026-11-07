# Bootstrap, applied only by a human as mtd-admin with `make bootstrap-apply`
# (human step H6, docs/runbook.md). Creates what CI needs before it can run
# Terraform itself: the remote state bucket, the GitHub OIDC provider (or
# reuses the account's existing one) and the CI roles. It also holds the
# account-level money and quota settings CI must not change: the budget, the
# cost allocation tag and the Lambda concurrency quota.

data "aws_caller_identity" "current" {}

locals {
  # Account id keeps the globally unique name stable without hard-coding it.
  state_bucket_name = "mtd-tfstate-${data.aws_caller_identity.current.account_id}"
}

# ---- Remote state bucket ----------------------------------------------------

resource "aws_s3_bucket" "state" {
  bucket = local.state_bucket_name

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_ownership_controls" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    id     = "expire-old-state-versions"
    status = "Enabled"
    filter {}

    noncurrent_version_expiration {
      noncurrent_days           = 90
      newer_noncurrent_versions = 20
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  depends_on = [aws_s3_bucket_versioning.state]
}

data "aws_iam_policy_document" "state_tls_only" {
  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    resources = [aws_s3_bucket.state.arn, "${aws_s3_bucket.state.arn}/*"]
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "state" {
  bucket = aws_s3_bucket.state.id
  policy = data.aws_iam_policy_document.state_tls_only.json

  depends_on = [aws_s3_bucket_public_access_block.state]
}

# ---- GitHub OIDC provider ---------------------------------------------------

# An account can hold only one provider per URL. Reuse an existing one unless
# this account has none.
resource "aws_iam_openid_connect_provider" "github" {
  count          = var.create_github_oidc_provider ? 1 : 0
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}

data "aws_iam_openid_connect_provider" "github" {
  count = var.create_github_oidc_provider ? 0 : 1
  url   = "https://token.actions.githubusercontent.com"
}

locals {
  oidc_provider_arn = var.create_github_oidc_provider ? aws_iam_openid_connect_provider.github[0].arn : data.aws_iam_openid_connect_provider.github[0].arn
}

# ---- CI roles -------------------------------------------------------------

module "ci_role" {
  source = "../modules/ci_role"

  aws_region               = var.aws_region
  github_subject_prefix    = var.github_subject_prefix
  oidc_provider_arn        = local.oidc_provider_arn
  state_bucket_arn         = aws_s3_bucket.state.arn
  enforce_workflow_binding = var.enforce_workflow_binding
}

# ---- Money and quota ------------------------------------------------------

# The project's own spend: the filter matches the `project` cost allocation
# tag, so the budget only sees costs from the day that tag is active (below).
# Budgets are priced in USD.
resource "aws_budgets_budget" "monthly" {
  name         = "mind-the-data-monthly"
  budget_type  = "COST"
  limit_amount = format("%.2f", var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  cost_filter {
    name   = "TagKeyValue"
    values = ["user:project$mind-the-data"]
  }

  dynamic "notification" {
    for_each = toset([50, 80, 100])
    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value
      threshold_type             = "PERCENTAGE"
      notification_type          = "ACTUAL"
      subscriber_email_addresses = [var.alert_email]
    }
  }

  # Warns before the month ends rather than after.
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.alert_email]
  }
}

# Activation only succeeds once the tag key has appeared in billing data
# (it has: Cost Explorer lists `project` as Inactive). Costs are attributed
# from activation onwards, not backdated.
resource "aws_ce_cost_allocation_tag" "project" {
  tag_key = "project"
  status  = "Active"
}

# The account default is 10, which leaves nothing to reserve for the poller
# (ADR-006). A request for 1,000 was opened by hand on 2026-11-06. Importing
# adopts the quota instead of creating it: creating would send a second
# request, which Service Quotas refuses while one is open and which would
# fail the apply. After the import the plan shows an in-place update from
# the current value to 1,000; while a request is open, that update ends with
# a warning ("already exists") and no new request. The diff disappears once
# AWS approves.
import {
  to = aws_servicequotas_service_quota.lambda_concurrency
  id = "lambda/L-B99A9384"
}

resource "aws_servicequotas_service_quota" "lambda_concurrency" {
  service_code = "lambda"
  quota_code   = "L-B99A9384"
  value        = var.lambda_concurrency_quota
}
