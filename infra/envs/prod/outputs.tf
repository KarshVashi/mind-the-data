output "raw_bucket_name" {
  description = "Raw bucket the collector writes to."
  value       = module.raw_bucket.bucket_name
}

output "poller_function_name" {
  description = "Name of the per-minute poller Lambda."
  value       = module.poller.function_name
}

output "reference_function_name" {
  description = "Name of the weekly reference Lambda."
  value       = module.reference.function_name
}

output "alerts_topic_arn" {
  description = "SNS topic the collector alarms notify."
  value       = module.alarms.topic_arn
}
