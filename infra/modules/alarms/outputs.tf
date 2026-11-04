output "topic_arn" {
  description = "ARN of the alerts SNS topic."
  value       = aws_sns_topic.alerts.arn
}

output "alarm_names" {
  description = "Names of every alarm the module creates."
  value = concat(
    [
      aws_cloudwatch_metric_alarm.poller_errors.alarm_name,
      aws_cloudwatch_metric_alarm.poller_throttles.alarm_name,
      aws_cloudwatch_metric_alarm.request_failures.alarm_name,
      aws_cloudwatch_metric_alarm.reference_errors.alarm_name,
    ],
    [for a in aws_cloudwatch_metric_alarm.no_objects : a.alarm_name],
  )
}
