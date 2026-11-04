# Collector alarms and the SNS topic they notify. Silent data loss is the main
# risk (PLAN section 12), so the "no objects written" alarms treat missing
# data as breaching: a poller that stops running emits nothing at all.

resource "aws_sns_topic" "alerts" {
  name = var.topic_name
}

# Email subscriptions stay pending until the link in the confirmation email
# is clicked; Terraform cannot confirm them.
resource "aws_sns_topic_subscription" "email" {
  count = nonsensitive(var.alert_email != "") ? 1 : 0

  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

locals {
  actions = [aws_sns_topic.alerts.arn]
}

# Share of poller invocations that ended in an unhandled error. A request
# that TfL rejects is not a Lambda error (it is written to raw as a failure
# envelope), so TfL-side failures are covered by the request failure alarm.
resource "aws_cloudwatch_metric_alarm" "poller_errors" {
  alarm_name          = "${var.poller_function_name}-error-rate"
  alarm_description   = "Poller invocations are failing: ${var.error_rate_threshold_percent}% or more in ${var.error_rate_datapoints} of the last ${var.error_rate_periods} minutes."
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = var.error_rate_threshold_percent
  evaluation_periods  = var.error_rate_periods
  datapoints_to_alarm = var.error_rate_datapoints
  # No invocations means no rate; the no-objects alarms catch that case.
  treat_missing_data = "notBreaching"
  alarm_actions      = local.actions
  ok_actions         = local.actions

  metric_query {
    id          = "error_rate"
    expression  = "100 * errors / invocations"
    label       = "Poller error rate (%)"
    return_data = true
  }

  metric_query {
    id = "errors"
    metric {
      namespace   = "AWS/Lambda"
      metric_name = "Errors"
      dimensions  = { FunctionName = var.poller_function_name }
      period      = 60
      stat        = "Sum"
    }
  }

  metric_query {
    id = "invocations"
    metric {
      namespace   = "AWS/Lambda"
      metric_name = "Invocations"
      dimensions  = { FunctionName = var.poller_function_name }
      period      = 60
      stat        = "Sum"
    }
  }
}

# A throttle means an invocation got no concurrency slot (a poll overlapping
# the one before it under reserved concurrency, or the account limit), so
# that minute may not have been polled.
resource "aws_cloudwatch_metric_alarm" "poller_throttles" {
  alarm_name          = "${var.poller_function_name}-throttles"
  alarm_description   = "Poller invocations were throttled in the last 5 minutes."
  namespace           = "AWS/Lambda"
  metric_name         = "Throttles"
  dimensions          = { FunctionName = var.poller_function_name }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.actions
  ok_actions          = local.actions
}

# The code logs ObjectsWritten (Feed=<feed>) as an Embedded Metric Format
# line after each successful S3 write. A crashed or skipped poll logs
# nothing, which is why missing data counts as breaching.
resource "aws_cloudwatch_metric_alarm" "no_objects" {
  for_each = toset(var.no_write_feeds)

  alarm_name          = "mind-the-data-no-objects-${replace(each.key, "_", "-")}"
  alarm_description   = "No ${each.key} objects written to raw for ${var.no_write_minutes} minutes: the collector is losing data."
  namespace           = var.metric_namespace
  metric_name         = "ObjectsWritten"
  dimensions          = { Feed = each.key }
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = var.no_write_minutes
  datapoints_to_alarm = var.no_write_minutes
  comparison_operator = "LessThanThreshold"
  threshold           = 1
  treat_missing_data  = "breaching"
  alarm_actions       = local.actions
  ok_actions          = local.actions
}

# Line status is one request per poll, so sustained failures mean TfL is
# rejecting us (for example a revoked or disabled app key) or is down. Raw
# still gets failure envelopes, so ObjectsWritten alone would stay green.
resource "aws_cloudwatch_metric_alarm" "request_failures" {
  alarm_name          = "mind-the-data-tfl-failures-${replace(var.request_failure_feed, "_", "-")}"
  alarm_description   = "Every ${var.request_failure_feed} request to TfL failed for ${var.request_failure_minutes} minutes."
  namespace           = var.metric_namespace
  metric_name         = "RequestFailures"
  dimensions          = { Feed = var.request_failure_feed }
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = var.request_failure_minutes
  datapoints_to_alarm = var.request_failure_minutes
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  # A minute without a failed request may have no RequestFailures datapoint.
  treat_missing_data = "notBreaching"
  alarm_actions      = local.actions
  ok_actions         = local.actions
}

# The weekly reference fetch runs once; any error in the hour after it is
# worth an email.
resource "aws_cloudwatch_metric_alarm" "reference_errors" {
  alarm_name          = "${var.reference_function_name}-errors"
  alarm_description   = "The weekly reference fetch failed."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = var.reference_function_name }
  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  treat_missing_data  = "notBreaching"
  alarm_actions       = local.actions
}
