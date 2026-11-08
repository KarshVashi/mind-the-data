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

# ---- Catalog and dbt ------------------------------------------------------------

output "raw_glue_database" {
  description = "Glue database with one raw table per feed (dbt sources)."
  value       = module.catalog.database_name
}

output "raw_glue_tables" {
  description = "Raw tables in raw_glue_database."
  value       = module.catalog.table_names
}

output "athena_results_bucket_name" {
  description = "Bucket that receives Athena query results; objects expire after athena_results_expiration_days."
  value       = module.athena_results_bucket.bucket_name
}

output "athena_results_bucket_arn" {
  description = "ARN of the Athena query results bucket."
  value       = module.athena_results_bucket.bucket_arn
}

output "curated_bucket_name" {
  description = "Bucket for dbt's Iceberg tables, under dev/ and prod/."
  value       = module.curated_bucket.bucket_name
}

output "curated_bucket_arn" {
  description = "ARN of the curated bucket."
  value       = module.curated_bucket.bucket_arn
}

output "athena_workgroups" {
  description = "Athena workgroups: one per dbt target, and ops for the health checks."
  value       = module.athena.workgroup_names
}

# The workgroups enforce their own result location, so the staging dir is
# overridden by Athena; it is set to the same place so dbt's view of where
# results land is the truth.
output "dbt_athena_env" {
  description = "dbt connection settings per target: DBT_ATHENA_* environment variables for env.example (dev) and repository variables (prod)."
  value = merge([
    for target in local.dbt_targets : {
      "DBT_ATHENA_S3_STAGING_DIR_${upper(target)}" = module.athena.results_locations["${local.prefix}-${target}"]
      "DBT_ATHENA_S3_DATA_DIR_${upper(target)}"    = "s3://${local.curated_bucket_name}/${target}/"
      "DBT_ATHENA_WORKGROUP_${upper(target)}"      = "${local.prefix}-${target}"
      "DBT_ATHENA_SCHEMA_${upper(target)}"         = "${local.glue_prefix}_${target}"
    }
  ]...)
}
