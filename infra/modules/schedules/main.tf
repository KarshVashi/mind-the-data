# EventBridge Scheduler schedules that invoke Lambda functions through one
# shared role. Scheduler calls lambda:InvokeFunction as that role, so the
# functions need no resource policy (CI is denied lambda:AddPermission).

data "aws_caller_identity" "current" {}

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }

    # Only schedules in this account may use the role.
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_iam_role" "scheduler" {
  name                 = var.role_name
  description          = "EventBridge Scheduler: invokes the mind-the-data Lambdas."
  assume_role_policy   = data.aws_iam_policy_document.assume.json
  permissions_boundary = var.permissions_boundary_arn
}

data "aws_iam_policy_document" "invoke" {
  statement {
    sid       = "InvokeScheduledFunctions"
    actions   = ["lambda:InvokeFunction"]
    resources = sort(distinct([for s in values(var.schedules) : s.function_arn]))
  }
}

resource "aws_iam_role_policy" "invoke" {
  name   = "invoke"
  role   = aws_iam_role.scheduler.id
  policy = data.aws_iam_policy_document.invoke.json
}

resource "aws_scheduler_schedule" "this" {
  for_each = var.schedules

  name                         = each.key
  description                  = each.value.description
  schedule_expression          = each.value.expression
  schedule_expression_timezone = each.value.timezone

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = each.value.function_arn
    role_arn = aws_iam_role.scheduler.arn

    retry_policy {
      maximum_retry_attempts       = each.value.retry_attempts
      maximum_event_age_in_seconds = each.value.max_event_age_seconds
    }
  }

  depends_on = [aws_iam_role_policy.invoke]
}
