# Production stack. Deployed by the infra workflow when a pull request is
# merged into main (ADR-005). CI role permissions live in infra/bootstrap.
#
# The collector: a poller Lambda every minute and a reference Lambda weekly,
# both writing gzipped NDJSON to the raw bucket (ADR-003), with alarms on
# errors, throttles and missing writes.

data "aws_caller_identity" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  prefix     = "mind-the-data"

  # Built from the name rather than read from the bucket resource, so the
  # Lambda environment and IAM policies are readable in plans before the
  # bucket exists.
  raw_bucket_name = "${local.prefix}-raw-${local.account_id}"
  raw_bucket_arn  = "arn:aws:s3:::${local.raw_bucket_name}"

  # Fixed names: infra/modules/ci_role builds the transform role's ARNs from
  # the same patterns, so change both together.
  results_bucket_name = "${local.prefix}-athena-results-${local.account_id}"
  curated_bucket_name = "${local.prefix}-curated-${local.account_id}"

  # Referenced by ARN only. Reading it through a data source would put the
  # key in state, which every pull request plan can read (ADR-005).
  app_key_parameter_arn = "arn:aws:ssm:${var.aws_region}:${local.account_id}:parameter${var.tfl_app_key_parameter}"

  poller_feeds    = ["line_status", "line_disruption", "arrivals", "stop_disruption"]
  reference_feeds = ["ref_route_sequence", "ref_timetable", "ref_severity"]

  # Glue database names cannot contain hyphens.
  glue_prefix = replace(local.prefix, "-", "_")
  # Each dbt target gets a workgroup, a results prefix and a curated prefix.
  dbt_targets = ["dev", "prod"]

  common_env = {
    RAW_BUCKET            = local.raw_bucket_name
    TFL_APP_KEY_PARAMETER = var.tfl_app_key_parameter
    METRIC_NAMESPACE      = var.metric_namespace
    CONFIG_DIR            = "/var/task/config"
    LOG_LEVEL             = var.log_level
  }
}

# Created by the bootstrap; every role this stack creates must carry it.
# Looked up by ARN: a lookup by name needs iam:ListPolicies on every policy
# in the account, which the plan role does not have.
data "aws_iam_policy" "workload_boundary" {
  arn = "arn:aws:iam::${local.account_id}:policy/${var.workload_boundary_policy_name}"
}

module "raw_bucket" {
  source = "../../modules/raw_bucket"

  bucket_name = local.raw_bucket_name
}

# ---- Runtime permissions ------------------------------------------------------

# Custom metrics are written as Embedded Metric Format log lines, which
# CloudWatch turns into metrics, so no role needs cloudwatch:PutMetricData.
data "aws_iam_policy_document" "tfl_key" {
  statement {
    sid       = "ReadAppKey"
    actions   = ["ssm:GetParameter"]
    resources = [local.app_key_parameter_arn]
  }
  statement {
    sid       = "DecryptAppKey"
    actions   = ["kms:Decrypt"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["ssm.${var.aws_region}.amazonaws.com"]
    }
  }
}

# Each function may only add objects under its own feeds' prefixes; neither
# can read, list or delete raw.
data "aws_iam_policy_document" "poller" {
  source_policy_documents = [data.aws_iam_policy_document.tfl_key.json]
  statement {
    sid       = "WriteRaw"
    actions   = ["s3:PutObject"]
    resources = [for feed in local.poller_feeds : "${local.raw_bucket_arn}/raw/${feed}/*"]
  }
}

data "aws_iam_policy_document" "reference" {
  source_policy_documents = [data.aws_iam_policy_document.tfl_key.json]
  statement {
    sid       = "WriteRaw"
    actions   = ["s3:PutObject"]
    resources = [for feed in local.reference_feeds : "${local.raw_bucket_arn}/raw/${feed}/*"]
  }
}

# ---- Functions ----------------------------------------------------------------

module "poller" {
  source = "../../modules/lambda_function"

  function_name            = "${local.prefix}-poller"
  description              = "Polls TfL line status, disruptions and arrivals every minute into raw."
  handler                  = "mind_the_data.ingest.poller.handler"
  zip_path                 = var.lambda_zip_path
  memory_mb                = 512
  timeout_seconds          = 60
  reserved_concurrency     = var.poller_reserved_concurrency
  log_retention_days       = var.log_retention_days
  policy_json              = data.aws_iam_policy_document.poller.json
  permissions_boundary_arn = data.aws_iam_policy.workload_boundary.arn

  environment = merge(local.common_env, {
    POLL_BUDGET_SECONDS = "45"
    MAX_CONCURRENCY     = "10"
  })

  # A retried poll would run late and overlap the next minute's poll; the
  # next scheduled run is the retry.
  async_retry_attempts        = 0
  async_max_event_age_seconds = 60
}

module "reference" {
  source = "../../modules/lambda_function"

  function_name = "${local.prefix}-reference"
  description   = "Fetches TfL route sequences, timetables and severity codes weekly into raw."
  handler       = "mind_the_data.ingest.reference.handler"
  zip_path      = var.lambda_zip_path
  # Larger than the plan's 60 s / 512 MB: about 199 calls and up to ~560 MB
  # peak in the offline measurement (ADR-007).
  memory_mb                = 1024
  timeout_seconds          = 300
  log_retention_days       = var.log_retention_days
  policy_json              = data.aws_iam_policy_document.reference.json
  permissions_boundary_arn = data.aws_iam_policy.workload_boundary.arn

  # Concurrency 4 keeps the weekly fetch from crowding out the poller's
  # requests under the 500 per minute key limit.
  environment = merge(local.common_env, {
    POLL_BUDGET_SECONDS = "240"
    MAX_CONCURRENCY     = "4"
  })

  async_retry_attempts        = 2
  async_max_event_age_seconds = 3600
}

# ---- Schedules ----------------------------------------------------------------

module "schedules" {
  source = "../../modules/schedules"

  role_name                = "${local.prefix}-scheduler"
  permissions_boundary_arn = data.aws_iam_policy.workload_boundary.arn

  schedules = {
    "${local.prefix}-poller-every-minute" = {
      description           = "Invoke the poller every minute."
      expression            = "rate(1 minute)"
      function_arn          = module.poller.function_arn
      retry_attempts        = 0
      max_event_age_seconds = 60
      # A late run keeps its scheduled minute's cadence (stop disruption every 5).
      input = jsonencode({ scheduled_time = "<aws.scheduler.scheduled-time>" })
    }
    "${local.prefix}-reference-weekly" = {
      description           = "Invoke the reference fetch every Monday at 03:00 London time."
      expression            = "cron(0 3 ? * MON *)"
      timezone              = "Europe/London"
      function_arn          = module.reference.function_arn
      retry_attempts        = 2
      max_event_age_seconds = 3600
      input                 = jsonencode({ scheduled_time = "<aws.scheduler.scheduled-time>" })
    }
  }
}

# ---- Alarms -------------------------------------------------------------------

module "alarms" {
  source = "../../modules/alarms"

  topic_name              = "${local.prefix}-alerts"
  alert_email             = var.alert_email
  metric_namespace        = var.metric_namespace
  poller_function_name    = module.poller.function_name
  reference_function_name = module.reference.function_name
  no_write_feeds          = ["line_status", "line_disruption", "arrivals"]
  request_failure_feed    = "line_status"
}

# ---- Catalog and query engine (M2) --------------------------------------------
#
# Glue tables over raw for Athena, a workgroup per dbt target, and the two
# buckets dbt needs: query results (expired after a week) and the curated
# Iceberg tables. dbt creates its own target databases (mind_the_data_dev,
# mind_the_data_prod) with `create schema if not exists`, so they are not
# declared here.

module "catalog" {
  source = "../../modules/catalog"

  database_name = "${local.glue_prefix}_raw"
  bucket_name   = local.raw_bucket_name
  feeds         = concat(local.poller_feeds, local.reference_feeds)
}

module "athena_results_bucket" {
  source = "../../modules/data_bucket"

  bucket_name     = local.results_bucket_name
  expiration_days = var.athena_results_expiration_days
}

module "curated_bucket" {
  source = "../../modules/data_bucket"

  bucket_name = local.curated_bucket_name
}

module "athena" {
  source = "../../modules/athena"

  results_bucket_name            = local.results_bucket_name
  expected_bucket_owner          = local.account_id
  bytes_scanned_cutoff_per_query = var.athena_bytes_scanned_cutoff_per_query

  workgroups = {
    for target in local.dbt_targets : "${local.prefix}-${target}" => {
      description    = "dbt ${target} target and ad hoc ${target} queries."
      results_prefix = target
    }
  }
}
