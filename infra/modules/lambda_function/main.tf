# One Lambda function with its own execution role and log group. Callers pass
# the role's permissions as a policy document; this module adds only the
# right to write to the function's own log group.

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  log_group_name = "/aws/lambda/${var.function_name}"
  log_group_arn  = "arn:aws:logs:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:log-group:${local.log_group_name}"
}

# Created before the function so Lambda never auto-creates it without a
# retention period.
resource "aws_cloudwatch_log_group" "this" {
  name              = local.log_group_name
  retention_in_days = var.log_retention_days
}

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "this" {
  name                 = var.function_name
  description          = "Execution role for the ${var.function_name} Lambda."
  assume_role_policy   = data.aws_iam_policy_document.assume.json
  permissions_boundary = var.permissions_boundary_arn
}

data "aws_iam_policy_document" "logs" {
  # Creating the group is left out on purpose: Terraform owns it.
  statement {
    sid       = "OwnLogGroup"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${local.log_group_arn}:*"]
  }
}

data "aws_iam_policy_document" "combined" {
  source_policy_documents = [data.aws_iam_policy_document.logs.json, var.policy_json]
}

resource "aws_iam_role_policy" "this" {
  name   = "runtime"
  role   = aws_iam_role.this.id
  policy = data.aws_iam_policy_document.combined.json
}

resource "aws_lambda_function" "this" {
  function_name    = var.function_name
  description      = var.description
  role             = aws_iam_role.this.arn
  handler          = var.handler
  runtime          = "python3.12"
  architectures    = ["arm64"]
  filename         = var.zip_path
  source_code_hash = filebase64sha256(var.zip_path)
  memory_size      = var.memory_mb
  timeout          = var.timeout_seconds

  reserved_concurrent_executions = var.reserved_concurrency

  # No logging_config: keep the log format as Text. The code writes custom
  # metrics as Embedded Metric Format lines, which must reach CloudWatch
  # exactly as printed. The JSON format can reformat application log
  # records, and if CloudWatch then stops extracting the metrics, the
  # no-objects alarms fire.

  environment {
    variables = var.environment
  }

  # The role's policy must exist before the first invocation can use it.
  depends_on = [aws_cloudwatch_log_group.this, aws_iam_role_policy.this]
}

# Scheduler invokes asynchronously. Lambda's own retries would rerun a failed
# invocation minutes later, so they are set by the caller (none for the
# per-minute poller, whose next run is a minute away anyway).
resource "aws_lambda_function_event_invoke_config" "this" {
  function_name                = aws_lambda_function.this.function_name
  maximum_retry_attempts       = var.async_retry_attempts
  maximum_event_age_in_seconds = var.async_max_event_age_seconds
}
