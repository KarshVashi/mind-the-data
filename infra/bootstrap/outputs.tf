output "state_bucket" {
  description = "Remote state bucket; set as the TF_STATE_BUCKET repository variable."
  value       = aws_s3_bucket.state.bucket
}

output "plan_role_arn" {
  description = "Set as the AWS_PLAN_ROLE_ARN repository variable."
  value       = module.ci_role.plan_role_arn
}

output "apply_role_arn" {
  description = "Set as the AWS_ROLE_ARN repository variable."
  value       = module.ci_role.apply_role_arn
}

output "transform_role_arn" {
  description = "Set as the AWS_TRANSFORM_ROLE_ARN repository variable."
  value       = module.ci_role.transform_role_arn
}

output "observer_role_arn" {
  description = "Set as the AWS_OBSERVER_ROLE_ARN repository variable."
  value       = module.ci_role.observer_role_arn
}

output "publish_role_arn" {
  description = "Set as the AWS_PUBLISH_ROLE_ARN repository variable (M5)."
  value       = module.ci_role.publish_role_arn
}

output "workload_boundary_arn" {
  description = "Permissions boundary for every role the pipeline creates."
  value       = module.ci_role.workload_boundary_arn
}
