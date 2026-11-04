variable "function_name" {
  type        = string
  description = "Lambda function name; also names its role and log group. Must start with mind-the-data-."

  validation {
    condition     = startswith(var.function_name, "mind-the-data-")
    error_message = "CI can only manage functions and roles named mind-the-data-*."
  }
}

variable "description" {
  type        = string
  description = "Short description shown on the function."
}

variable "handler" {
  type        = string
  description = "Handler as module.function, e.g. mind_the_data.ingest.poller.handler."
}

variable "zip_path" {
  type        = string
  description = "Path to the deployment zip built by make package."
}

variable "memory_mb" {
  type        = number
  description = "Memory in MB."
  default     = 512
}

variable "timeout_seconds" {
  type        = number
  description = "Function timeout in seconds."
  default     = 60
}

variable "reserved_concurrency" {
  type        = number
  description = "Reserved concurrent executions; -1 leaves the function unreserved."
  default     = -1
}

variable "environment" {
  type        = map(string)
  description = "Environment variables for the function. Never put secrets here."
  default     = {}
}

variable "log_retention_days" {
  type        = number
  description = "Days to keep the function's CloudWatch logs."
  default     = 14
}

variable "policy_json" {
  type        = string
  description = "IAM policy document with the function's runtime permissions (logging is added by the module)."
}

variable "permissions_boundary_arn" {
  type        = string
  description = "ARN of the workload permissions boundary every pipeline role must carry (ADR-005)."
}

variable "async_retry_attempts" {
  type        = number
  description = "Lambda retries for a failed asynchronous invocation (0 to 2)."
  default     = 2
}

variable "async_max_event_age_seconds" {
  type        = number
  description = "Oldest an asynchronous event may be before Lambda drops it (60 to 21600)."
  default     = 21600
}
