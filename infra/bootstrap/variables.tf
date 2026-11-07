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

variable "enforce_workflow_binding" {
  type        = bool
  description = "Bind the plan, apply and transform roles to their workflow files (ADR-010). Turn on in a follow-up change once the trust probe has passed."
  default     = false
}

variable "alert_email" {
  type        = string
  description = "Address for budget alerts; the same as the ALERT_EMAIL repository variable. scripts/bootstrap.sh reads it from there."

  validation {
    condition     = can(regex("^[^@[:space:]]+@[^@[:space:]]+\\.[^@[:space:]]+$", var.alert_email))
    error_message = "Set alert_email to an email address (TF_VAR_alert_email, or the ALERT_EMAIL repository variable)."
  }
}

variable "monthly_budget_usd" {
  type        = number
  description = "Monthly budget for the project's tagged spend, in USD (Budgets works in USD). Plan H5 sets 5 GBP a month; 6.50 USD is about that."
  default     = 6.5
}

variable "lambda_concurrency_quota" {
  type        = number
  description = "Account-wide Lambda concurrent executions quota (L-B99A9384) to hold or request in this region."
  default     = 1000
}

variable "create_github_oidc_provider" {
  type        = bool
  description = "Create the token.actions.githubusercontent.com OIDC provider. Leave false when the account already has one (there can be only one)."
  default     = false
}
