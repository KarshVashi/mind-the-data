variable "workgroups" {
  type = map(object({
    description    = string
    results_prefix = string
  }))
  description = "Workgroups keyed by name (mind-the-data-*, so CI can manage them), each with the results-bucket prefix its query results go to."

  validation {
    condition     = alltrue([for name, wg in var.workgroups : startswith(name, "mind-the-data-") && can(regex("^[a-z0-9-]+$", wg.results_prefix))])
    error_message = "Workgroup names must start with mind-the-data-; results prefixes are lower-case with no slashes."
  }
}

variable "results_bucket_name" {
  type        = string
  description = "Bucket that receives query results."
}

variable "expected_bucket_owner" {
  type        = string
  description = "Account id that must own the results bucket; Athena refuses to write results to a bucket owned by anyone else."
}

variable "bytes_scanned_cutoff_per_query" {
  type        = number
  description = "Per-query scan limit in bytes; Athena cancels a query that scans more. Defaults to 10 GiB."
  default     = 10737418240

  validation {
    condition     = var.bytes_scanned_cutoff_per_query >= 10485760
    error_message = "Athena's minimum is 10 MiB (10485760 bytes)."
  }
}
