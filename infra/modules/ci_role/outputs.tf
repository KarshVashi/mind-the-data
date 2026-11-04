output "plan_role_arn" {
  description = "Role for pull request plans; set as the AWS_PLAN_ROLE_ARN repository variable."
  value       = aws_iam_role.plan.arn
}

output "apply_role_arn" {
  description = "Role for deploys on main; set as the AWS_ROLE_ARN repository variable."
  value       = aws_iam_role.apply.arn
}

output "transform_role_arn" {
  description = "Role for the scheduled dbt build on main; set as the AWS_TRANSFORM_ROLE_ARN repository variable."
  value       = aws_iam_role.transform.arn
}

output "workload_boundary_arn" {
  description = "Permissions boundary every pipeline-created role must carry."
  value       = aws_iam_policy.workload_boundary.arn
}
