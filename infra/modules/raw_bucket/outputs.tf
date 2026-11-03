output "bucket_name" {
  description = "Name of the raw bucket."
  value       = aws_s3_bucket.raw.id
}

output "bucket_arn" {
  description = "ARN of the raw bucket."
  value       = aws_s3_bucket.raw.arn
}
