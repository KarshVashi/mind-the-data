variable "aws_region" {
  type        = string
  description = "Region for every production resource."
  default     = "eu-west-2"
}

variable "lambda_zip_path" {
  type        = string
  description = "Deployment zip built by `make package`, relative to this directory (terraform -chdir runs here)."
  default     = "../../../dist/lambda.zip"
}

variable "alert_email" {
  type        = string
  description = "Email address for alarm notifications (ALERT_EMAIL repository variable); empty creates no subscription."
  default     = ""
  sensitive   = true
}

variable "tfl_app_key_parameter" {
  type        = string
  description = "SSM SecureString parameter holding the TfL app key (runbook H8). Read by the Lambdas at runtime, never by Terraform."
  default     = "/mind-the-data/tfl/app_key"

  validation {
    condition     = startswith(var.tfl_app_key_parameter, "/mind-the-data/")
    error_message = "The workload boundary only allows parameters under /mind-the-data/."
  }
}

variable "metric_namespace" {
  type        = string
  description = "CloudWatch namespace of the collector's Embedded Metric Format metrics, passed to the Lambdas and used by the alarms."
  default     = "MindTheData"
}

variable "log_level" {
  type        = string
  description = "LOG_LEVEL passed to the Lambdas."
  default     = "INFO"
}

variable "log_retention_days" {
  type        = number
  description = "Days to keep Lambda logs."
  default     = 14
}

variable "workload_boundary_policy_name" {
  type        = string
  description = "Name of the permissions boundary policy created by infra/bootstrap."
  default     = "mtd-workload-boundary"
}

variable "poller_reserved_concurrency" {
  type        = number
  description = "Reserved concurrency for the poller; -1 leaves it unreserved. See ADR-006 before changing."
  default     = -1

  validation {
    condition     = var.poller_reserved_concurrency == -1 || var.poller_reserved_concurrency == 1
    error_message = "Use 1 (one poll at a time) or -1 (unreserved, while the account concurrency quota is too low)."
  }
}

variable "athena_results_expiration_days" {
  type        = number
  description = "Days to keep Athena query results before they expire (plan section 11)."
  default     = 7
}

variable "athena_bytes_scanned_cutoff_per_query" {
  type        = number
  description = "Per-query scan limit for every workgroup, in bytes (plan section 11: 10 GiB)."
  default     = 10737418240
}
