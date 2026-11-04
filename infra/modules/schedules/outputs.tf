output "role_arn" {
  description = "ARN of the role Scheduler uses to invoke the functions."
  value       = aws_iam_role.scheduler.arn
}

output "schedule_arns" {
  description = "ARNs of the schedules, keyed by name."
  value       = { for name, s in aws_scheduler_schedule.this : name => s.arn }
}
