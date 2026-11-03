variable "bucket_name" {
  type        = string
  description = "Bucket name; must match mind-the-data-* so CI can manage it, and must not be a raw bucket."

  validation {
    condition     = startswith(var.bucket_name, "mind-the-data-") && !startswith(var.bucket_name, "mind-the-data-raw-")
    error_message = "Use a mind-the-data-* name; raw buckets use the raw_bucket module, which never expires objects."
  }
}

variable "expiration_days" {
  type        = number
  description = "Delete every object this many days after it is written; null keeps objects indefinitely."
  default     = null

  validation {
    condition     = var.expiration_days == null || try(var.expiration_days >= 1, false)
    error_message = "Use null or a whole number of days, at least 1."
  }
}
