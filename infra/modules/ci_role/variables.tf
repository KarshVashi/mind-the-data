variable "aws_region" {
  type        = string
  description = "Region the project's resources live in; used to build resource ARNs."
}

variable "github_subject_prefix" {
  type        = string
  description = "Start of the OIDC subject GitHub sends for this repository, without the trailing context. With immutable subjects (the repository's setting) it carries the owner and repository ids, e.g. repo:owner@123/name@456. Read it with: gh api repos/OWNER/REPO/actions/oidc/customization/sub (sub_claim_prefix)."

  validation {
    condition     = can(regex("^repo:[A-Za-z0-9-]+(@[0-9]+)?/[A-Za-z0-9._-]+(@[0-9]+)?$", var.github_subject_prefix))
    error_message = "Use repo:owner/name, or repo:owner@id/name@id for immutable subjects."
  }
}

variable "oidc_provider_arn" {
  type        = string
  description = "ARN of the account's token.actions.githubusercontent.com OIDC provider."
}

variable "apply_branch" {
  type        = string
  description = "Branch whose workflow runs may assume the apply role; merging into it is the approval."
  default     = "main"
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
