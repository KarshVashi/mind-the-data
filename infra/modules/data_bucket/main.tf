# A private, SSE-S3, TLS-only project bucket for derived data: the curated
# Iceberg tables and Athena query results. Raw has its own module, because
# raw must never carry an expiration rule.
#
# Versioning is not declared: CI is denied s3:PutBucketVersioning on every
# project bucket (ADR-005). Iceberg keeps its own snapshots, and everything
# here can be rebuilt from raw.

resource "aws_s3_bucket" "this" {
  bucket        = var.bucket_name
  force_destroy = false

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_ownership_controls" "this" {
  bucket = aws_s3_bucket.this.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_public_access_block" "this" {
  bucket = aws_s3_bucket.this.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  bucket = aws_s3_bucket.this.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "this" {
  bucket = aws_s3_bucket.this.id

  # Athena and dbt write with multipart uploads; a failed query can leave
  # parts behind that are billed but never visible.
  rule {
    id     = "abort-incomplete-uploads"
    status = "Enabled"

    filter {}

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  dynamic "rule" {
    for_each = var.expiration_days == null ? [] : [var.expiration_days]
    content {
      id     = "expire-after-${rule.value}-days"
      status = "Enabled"

      filter {}

      expiration {
        days = rule.value
      }
    }
  }
}

data "aws_iam_policy_document" "this" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.this.arn, "${aws_s3_bucket.this.arn}/*"]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "this" {
  bucket = aws_s3_bucket.this.id
  policy = data.aws_iam_policy_document.this.json

  # S3 rejects a bucket policy while the public access block is being set.
  depends_on = [aws_s3_bucket_public_access_block.this]
}
