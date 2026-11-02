output "plan_role_arn" {
  description = "Role for pull request plans; set as the AWS_PLAN_ROLE_ARN repository variable."
  value       = aws_iam_role.plan.arn
}

output "apply_role_arn" {
  description = "Role for production applies; set as the AWS_ROLE_ARN repository variable. Null until enable_apply_role is true."
  value       = one(aws_iam_role.apply[*].arn)
}

output "workload_boundary_arn" {
  description = "Permissions boundary every pipeline-created role must carry."
  value       = aws_iam_policy.workload_boundary.arn
}
