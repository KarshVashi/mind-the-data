variable "topic_name" {
  type        = string
  description = "Name of the SNS topic alarms notify."
  default     = "mind-the-data-alerts"
}

variable "alert_email" {
  type        = string
  description = "Email address subscribed to the alerts topic; empty creates no subscription."
  default     = ""
  sensitive   = true
}

variable "metric_namespace" {
  type        = string
  description = "CloudWatch namespace of the collector's custom metrics."
  default     = "MindTheData"
}

variable "poller_function_name" {
  type        = string
  description = "Name of the per-minute poller Lambda."
}

variable "reference_function_name" {
  type        = string
  description = "Name of the daily reference Lambda."
}

variable "context_function_name" {
  type        = string
  description = "Name of the per-minute context Lambda (ADR-019); empty creates no alarm for it."
  default     = ""
}

variable "error_rate_threshold_percent" {
  type        = number
  description = "Error rate of the poller and the context collector, in percent of invocations per minute, at or above which a minute counts as failing."
  default     = 50
}

variable "error_rate_periods" {
  type        = number
  description = "Number of one-minute periods the error rate alarm looks back over."
  default     = 5
}

variable "error_rate_datapoints" {
  type        = number
  description = "Failing minutes within error_rate_periods that set off the error rate alarm."
  default     = 3
}

variable "no_write_feeds" {
  type        = list(string)
  description = "Feeds whose ObjectsWritten metric must be at least 1 every minute."
  default     = ["line_status", "line_disruption", "arrivals"]
}

variable "no_write_minutes" {
  type        = number
  description = "Consecutive minutes without a written object before the no-objects alarm fires."
  default     = 10
}

variable "request_failure_feed" {
  type        = string
  description = "Feed whose RequestFailures metric is alarmed on."
  default     = "line_status"
}

variable "request_failure_minutes" {
  type        = number
  description = "Consecutive minutes with failed requests before the request failure alarm fires."
  default     = 5
}
