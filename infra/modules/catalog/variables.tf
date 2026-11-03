variable "database_name" {
  type        = string
  description = "Glue database holding the raw tables; must match mind_the_data_* so CI can manage it."
  default     = "mind_the_data_raw"

  validation {
    condition     = startswith(var.database_name, "mind_the_data_")
    error_message = "CI may only manage Glue databases named mind_the_data_*."
  }
}

variable "bucket_name" {
  type        = string
  description = "Raw bucket the tables read from."
}

variable "feeds" {
  type        = list(string)
  description = "Raw feeds; each becomes a table of the same name over s3://<bucket>/raw/<feed>/."

  validation {
    condition     = alltrue([for f in var.feeds : can(regex("^[a-z][a-z0-9_]*$", f))])
    error_message = "Feed names must be lower-case snake_case, as they are also table names."
  }
}

variable "projection_start_date" {
  type        = string
  description = "First dt (yyyy-MM-dd) partition projection generates; earlier dates are never scanned."
  default     = "2026-11-01"

  validation {
    condition     = can(regex("^\\d{4}-\\d{2}-\\d{2}$", var.projection_start_date))
    error_message = "Use yyyy-MM-dd."
  }
}
