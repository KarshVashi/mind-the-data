# Raw TfL responses (ADR-003). Raw is the only irreplaceable data in the
# project, so the bucket cannot be destroyed from Terraform and has no
# expiration rule. Versioning is deliberately not declared: objects are
# write-once, and CI is denied s3:PutBucketVersioning (ADR-005), so even a
# "Disabled" versioning resource would fail to deploy.

resource "aws_s3_bucket" "raw" {
  bucket        = var.bucket_name
  force_destroy = false

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_ownership_controls" "raw" {
  bucket = aws_s3_bucket.raw.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_public_access_block" "raw" {
  bucket = aws_s3_bucket.raw.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "raw" {
  bucket = aws_s3_bucket.raw.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Objects under 128 KB are not transitioned (the S3 default), because
# Intelligent-Tiering never moves them out of its frequent tier; the
# transition request would cost money for no saving.
resource "aws_s3_bucket_lifecycle_configuration" "raw" {
  bucket                                 = aws_s3_bucket.raw.id
  transition_default_minimum_object_size = "all_storage_classes_128K"

  # Transition only. An expiration rule here would delete irreplaceable raw
  # data, and CI is able to set one (ADR-005), so review changes with care.

  rule {
    id     = "raw-to-intelligent-tiering"
    status = "Enabled"

    filter {
      prefix = "raw/"
    }

    transition {
      days          = var.intelligent_tiering_after_days
      storage_class = "INTELLIGENT_TIERING"
    }
  }
}

data "aws_iam_policy_document" "raw" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.raw.arn, "${aws_s3_bucket.raw.arn}/*"]

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

resource "aws_s3_bucket_policy" "raw" {
  bucket = aws_s3_bucket.raw.id
  policy = data.aws_iam_policy_document.raw.json

  # S3 rejects a bucket policy while the public access block is being set.
  depends_on = [aws_s3_bucket_public_access_block.raw]
}
