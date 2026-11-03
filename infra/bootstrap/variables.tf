variable "aws_region" {
  type        = string
  description = "Region for the state bucket and every project resource."
  default     = "eu-west-2"
}

variable "github_repository" {
  type        = string
  description = "GitHub repository allowed to assume the CI roles, as owner/name."
  default     = "KarshVashi/mind-the-data"
}

variable "create_github_oidc_provider" {
  type        = bool
  description = "Create the token.actions.githubusercontent.com OIDC provider. Leave false when the account already has one (there can be only one)."
  default     = false
}
