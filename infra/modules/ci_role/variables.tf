variable "aws_region" {
  type        = string
  description = "Region the project's resources live in; used to build resource ARNs."
}

variable "github_repository" {
  type        = string
  description = "GitHub repository allowed to assume the roles, as owner/name."

  validation {
    condition     = can(regex("^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$", var.github_repository))
    error_message = "Use the form owner/name."
  }
}

variable "oidc_provider_arn" {
  type        = string
  description = "ARN of the account's token.actions.githubusercontent.com OIDC provider."
}

variable "enable_apply_role" {
  type        = bool
  description = "Create the apply role. Only enable once the GitHub environment's required reviewer and branch rule are enforced (needs a public repository, or GitHub Enterprise for a private one)."
  default     = false
}

variable "apply_environment" {
  type        = string
  description = "GitHub environment whose jobs may assume the apply role."
  default     = "production"
}

variable "state_bucket_arn" {
  type        = string
  description = "ARN of the Terraform remote state bucket."
}

variable "state_key_prefix" {
  type        = string
  description = "Key prefix in the state bucket that CI may read and write."
  default     = "envs/"
}

variable "resource_prefix" {
  type        = string
  description = "Name prefix of every resource the pipeline may manage."
  default     = "mind-the-data"
}

variable "ci_name_prefix" {
  type        = string
  description = "Name prefix for the CI roles and boundary; must differ from resource_prefix so CI cannot edit them."
  default     = "mtd"
}

variable "metric_namespace" {
  type        = string
  description = "CloudWatch namespace workloads may publish custom metrics to."
  default     = "MindTheData"
}
