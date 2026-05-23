data "archive_file" "verify_lambda_code" {
  type        = "zip"
  output_path = "${path.module}/build/verify_lambda.zip"

  dynamic "source" {
    for_each = fileset("${path.module}/../..", "src/{orchestration,common}/**/*.py")
    content {
      content  = file("${path.module}/../../${source.value}")
      filename = source.value
    }
  }
}

data "aws_iam_policy_document" "verify_lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "verify_lambda" {
  name               = "${var.project_name}-verify"
  assume_role_policy = data.aws_iam_policy_document.verify_lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "verify_lambda_basic" {
  role       = aws_iam_role.verify_lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "verify_lambda_inline" {
  statement {
    sid = "AthenaQuery"
    actions = [
      "athena:StartQueryExecution",
      "athena:GetQueryExecution",
      "athena:GetQueryResults",
      "athena:StopQueryExecution",
      "athena:GetWorkGroup",
    ]
    resources = ["*"]
  }

  # Athena reads metadata from Glue Catalog, even for SELECT COUNT(*).
  statement {
    sid = "GlueCatalogRead"
    actions = [
      "glue:GetDatabase",
      "glue:GetDatabases",
      "glue:GetTable",
      "glue:GetTables",
      "glue:GetPartition",
      "glue:GetPartitions",
    ]
    resources = ["*"]
  }

  statement {
    sid     = "DataZonesRead"
    actions = ["s3:GetObject", "s3:ListBucket", "s3:GetBucketLocation"]
    resources = [
      aws_s3_bucket.data["staging"].arn,
      "${aws_s3_bucket.data["staging"].arn}/*",
      aws_s3_bucket.data["curated"].arn,
      "${aws_s3_bucket.data["curated"].arn}/*",
    ]
  }

  # Athena writes scan results back to the artifacts bucket.
  statement {
    sid = "AthenaResultsRW"
    actions = [
      "s3:PutObject",
      "s3:GetObject",
      "s3:ListBucket",
      "s3:GetBucketLocation",
    ]
    resources = [
      aws_s3_bucket.data["artifacts"].arn,
      "${aws_s3_bucket.data["artifacts"].arn}/*",
    ]
  }
}

resource "aws_iam_role_policy" "verify_lambda_inline" {
  name   = "${var.project_name}-verify-inline"
  role   = aws_iam_role.verify_lambda.id
  policy = data.aws_iam_policy_document.verify_lambda_inline.json
}

resource "aws_cloudwatch_log_group" "verify_lambda" {
  name              = "/aws/lambda/${var.project_name}-verify"
  retention_in_days = 14
}

resource "aws_lambda_function" "verify" {
  function_name    = "${var.project_name}-verify"
  role             = aws_iam_role.verify_lambda.arn
  runtime          = "python3.12"
  handler          = "src.orchestration.verify_lambda.handler"
  filename         = data.archive_file.verify_lambda_code.output_path
  source_code_hash = data.archive_file.verify_lambda_code.output_base64sha256
  memory_size      = 256
  timeout          = 120

  environment {
    variables = {
      ATHENA_OUTPUT_S3 = "s3://${aws_s3_bucket.data["artifacts"].id}/athena-results/"
      ATHENA_WORKGROUP = "primary"
      DELTA_THRESHOLD  = "0.05"
      STAGING_DATABASE = aws_glue_catalog_database.zones["staging"].name
      CURATED_DATABASE = aws_glue_catalog_database.zones["curated"].name
    }
  }

  depends_on = [aws_cloudwatch_log_group.verify_lambda]
}
