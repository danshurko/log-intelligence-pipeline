data "archive_file" "lambda_code" {
  type        = "zip"
  output_path = "${path.module}/build/lambda.zip"

  dynamic "source" {
    for_each = fileset("${path.module}/../..", "src/{ingestion,common}/**/*.py")
    content {
      content  = file("${path.module}/../../${source.value}")
      filename = source.value
    }
  }
}

# pyarrow alone is ~70 MB unzipped, and on macOS ARM `uv pip install` falls
# back to a source build because there's no native wheel. The runtime deps
# therefore ride in a separate layer built inside a Lambda-identical Linux
# x86_64 container. Run before the first apply:
#     make build-lambda-layer
# That target writes the layer zip at infra/terraform/build/lambda_layer.zip
# in the python/<package>/... shape the Lambda layer spec requires.
#
# The zip is uploaded to the artifacts bucket and Lambda is pointed at the
# S3 location: the inline PublishLayerVersion API caps at 70 MB but the
# S3-backed path raises the ceiling to 250 MB, which the pyarrow-bearing
# layer needs headroom for.
resource "aws_s3_object" "ingestion_deps_layer" {
  bucket = aws_s3_bucket.data["artifacts"].id
  key    = "lambda-layers/${var.project_name}-ingestion-deps.zip"
  source = "${path.module}/build/lambda_layer.zip"
  etag   = filemd5("${path.module}/build/lambda_layer.zip")
}

resource "aws_lambda_layer_version" "ingestion_deps" {
  layer_name          = "${var.project_name}-ingestion-deps"
  s3_bucket           = aws_s3_object.ingestion_deps_layer.bucket
  s3_key              = aws_s3_object.ingestion_deps_layer.key
  source_code_hash    = filebase64sha256("${path.module}/build/lambda_layer.zip")
  compatible_runtimes = ["python3.12"]
}

resource "aws_cloudwatch_log_group" "kinesis_consumer" {
  name              = "/aws/lambda/${var.project_name}-kinesis-consumer"
  retention_in_days = 14
}

resource "aws_lambda_function" "kinesis_consumer" {
  function_name    = "${var.project_name}-kinesis-consumer"
  role             = aws_iam_role.kinesis_consumer.arn
  runtime          = "python3.12"
  handler          = "src.ingestion.lambda_handler.handler"
  filename         = data.archive_file.lambda_code.output_path
  source_code_hash = data.archive_file.lambda_code.output_base64sha256
  memory_size      = 256
  timeout          = 30
  layers           = [aws_lambda_layer_version.ingestion_deps.arn]

  environment {
    variables = {
      RAW_BUCKET = aws_s3_bucket.data["raw"].id
    }
  }

  depends_on = [aws_cloudwatch_log_group.kinesis_consumer]
}

resource "aws_lambda_event_source_mapping" "kinesis_consumer" {
  event_source_arn                   = aws_kinesis_stream.device_logs.arn
  function_name                      = aws_lambda_function.kinesis_consumer.arn
  starting_position                  = "LATEST"
  batch_size                         = 100
  maximum_batching_window_in_seconds = 10
  parallelization_factor             = 1

  destination_config {
    on_failure {
      destination_arn = aws_sqs_queue.ingestion_dlq.arn
    }
  }
}
