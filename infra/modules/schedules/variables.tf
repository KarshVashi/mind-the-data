variable "role_name" {
  type        = string
  description = "Name of the role Scheduler assumes to invoke the functions."
  default     = "mind-the-data-scheduler"

  validation {
    condition     = startswith(var.role_name, "mind-the-data-")
    error_message = "CI can only manage roles named mind-the-data-*."
  }
}

variable "permissions_boundary_arn" {
  type        = string
  description = "ARN of the workload permissions boundary every pipeline role must carry (ADR-005)."
}

variable "schedules" {
  type = map(object({
    description           = string
    expression            = string
    timezone              = optional(string, "UTC")
    function_arn          = string
    retry_attempts        = number
    max_event_age_seconds = number
  }))
  description = "Schedules keyed by name (mind-the-data-*): expression, timezone, target function and retry policy."

  validation {
    condition     = alltrue([for name in keys(var.schedules) : startswith(name, "mind-the-data-")])
    error_message = "CI can only manage schedules named mind-the-data-*."
  }
}
