# Glue catalog over the raw bucket (ADR-003): one external table per feed,
# reading the gzipped NDJSON envelopes as they are written. Partitions are
# resolved from the path by partition projection, so there is no crawler and
# no partition to register when a new hour starts.
#
# The OpenX JSON SerDe returns a nested JSON value as its JSON text when the
# column is declared as string. It re-prints that value, so `payload` holds
# the same JSON value as the raw object but not necessarily the same text
# (key order, number formatting, escaping); the raw objects stay unchanged. The
# TfL response is parsed in dbt staging, not here. `case.insensitive` is off
# because the SerDe otherwise lower-cases every key inside that text as well
# (`modeName` becomes `modename`), and staging reads TfL's camelCase keys.
# The envelope's own keys are already lower case. `ignore.malformed.json` is
# left unset (false) on purpose: a malformed line fails the query instead of
# silently becoming a null row, because missing data must never pass as a
# quiet period.

locals {
  # Every raw object is one envelope per line; see ADR-003.
  envelope_columns = [
    { name = "poll_id", type = "string", comment = "Poll identifier, shared by every line in one object." },
    { name = "polled_at", type = "string", comment = "Poll start, ISO 8601 UTC with milliseconds and Z." },
    { name = "feed", type = "string", comment = "Feed name; equals the table name." },
    { name = "request", type = "struct<path:string>", comment = "Request path, without the app key." },
    { name = "http_status", type = "int", comment = "HTTP status; null when no response was received." },
    { name = "latency_ms", type = "int", comment = "Request latency in milliseconds." },
    { name = "error", type = "string", comment = "Failure type when no response was received; null on success." },
    { name = "payload", type = "string", comment = "TfL response as JSON text: the same value as in raw, formatting may differ." },
  ]
}

resource "aws_glue_catalog_database" "raw" {
  name        = var.database_name
  description = "Raw TfL envelopes (gzipped NDJSON) in ${var.bucket_name}, one table per feed."
}

resource "aws_glue_catalog_table" "raw" {
  for_each = toset(var.feeds)

  name          = each.key
  database_name = aws_glue_catalog_database.raw.name
  description   = "Raw ${each.key} envelopes, one row per TfL request. Partitions dt and hour are UTC."
  table_type    = "EXTERNAL_TABLE"

  parameters = {
    EXTERNAL                      = "TRUE"
    classification                = "json"
    "projection.enabled"          = "true"
    "projection.dt.type"          = "date"
    "projection.dt.format"        = "yyyy-MM-dd"
    "projection.dt.range"         = "${var.projection_start_date},NOW"
    "projection.dt.interval"      = "1"
    "projection.dt.interval.unit" = "DAYS"
    "projection.hour.type"        = "integer"
    "projection.hour.range"       = "0,23"
    "projection.hour.digits"      = "2"
    "storage.location.template"   = "s3://${var.bucket_name}/raw/${each.key}/dt=$${dt}/hour=$${hour}/"
  }

  partition_keys {
    name    = "dt"
    type    = "string"
    comment = "UTC date of the poll, yyyy-MM-dd."
  }

  partition_keys {
    name    = "hour"
    type    = "string"
    comment = "UTC hour of the poll, 00 to 23."
  }

  storage_descriptor {
    location = "s3://${var.bucket_name}/raw/${each.key}/"
    # Athena decompresses by the .gz extension.
    input_format  = "org.apache.hadoop.mapred.TextInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat"

    ser_de_info {
      name                  = "openx-json"
      serialization_library = "org.openx.data.jsonserde.JsonSerDe"
      parameters = {
        "case.insensitive" = "false"
      }
    }

    dynamic "columns" {
      for_each = local.envelope_columns
      content {
        name    = columns.value.name
        type    = columns.value.type
        comment = columns.value.comment
      }
    }
  }
}
