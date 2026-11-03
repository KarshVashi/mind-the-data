variable "aws_region" {
  type        = string
  description = "Region for the state bucket and every project resource."
  default     = "eu-west-2"
}

variable "github_subject_prefix" {
  type        = string
  description = "OIDC subject prefix GitHub sends for this repository (sub_claim_prefix from gh api repos/KarshVashi/mind-the-data/actions/oidc/customization/sub). The repository uses immutable subjects, which name the owner and repository by id, so a renamed or re-created repository cannot assume the roles."
  default     = "repo:KarshVashi@41749592/mind-the-data@1442302649"
}

variable "create_github_oidc_provider" {
  type        = bool
  description = "Create the token.actions.githubusercontent.com OIDC provider. Leave false when the account already has one (there can be only one)."
  default     = false
}
