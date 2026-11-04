# One Athena workgroup per dbt target. The workgroup settings are enforced,
# so a client cannot lift the per-query scan cap or send results elsewhere
# (plan section 11 cost guards). Each target's results go under its own
# prefix of the results bucket, which expires them.

resource "aws_athena_workgroup" "this" {
  for_each = var.workgroups

  name          = each.key
  description   = each.value.description
  state         = "ENABLED"
  force_destroy = false

  configuration {
    enforce_workgroup_configuration    = true
    publish_cloudwatch_metrics_enabled = true
    bytes_scanned_cutoff_per_query     = var.bytes_scanned_cutoff_per_query

    engine_version {
      selected_engine_version = "Athena engine version 3"
    }

    result_configuration {
      output_location       = "s3://${var.results_bucket_name}/${each.value.results_prefix}/"
      expected_bucket_owner = var.expected_bucket_owner

      encryption_configuration {
        encryption_option = "SSE_S3"
      }
    }
  }
}
