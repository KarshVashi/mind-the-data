variable "bucket_name" {
  type        = string
  description = "Name of the raw bucket; must match mind-the-data-raw-* so CI's raw-object protections apply."

  validation {
    condition     = startswith(var.bucket_name, "mind-the-data-raw-")
    error_message = "The raw bucket name must start with mind-the-data-raw-."
  }
}

variable "intelligent_tiering_after_days" {
  type        = number
  description = "Age in days at which raw objects move to S3 Intelligent-Tiering."
  default     = 30
}
