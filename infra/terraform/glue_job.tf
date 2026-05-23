# SQL transforms are deployed to the artifacts bucket and downloaded by
# the PySpark job at runtime. `source_hash = filemd5(...)` keeps the S3
# object in sync whenever the local file changes.
resource "aws_s3_object" "transform_sql" {
  for_each = fileset("${path.module}/../../sql/transforms", "*.sql")

  bucket      = aws_s3_bucket.data["artifacts"].id
  key         = "sql/transforms/${each.value}"
  source      = "${path.module}/../../sql/transforms/${each.value}"
  source_hash = filemd5("${path.module}/../../sql/transforms/${each.value}")
}

resource "aws_s3_object" "glue_pyspark_script" {
  bucket      = aws_s3_bucket.data["artifacts"].id
  key         = "glue/glue_pyspark_job.py"
  source      = "${path.module}/../../src/transformation/glue_pyspark_job.py"
  source_hash = filemd5("${path.module}/../../src/transformation/glue_pyspark_job.py")
}

data "aws_iam_policy_document" "glue_job_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["glue.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "glue_job" {
  name               = "${var.project_name}-glue-job"
  assume_role_policy = data.aws_iam_policy_document.glue_job_assume.json
}

resource "aws_iam_role_policy_attachment" "glue_job_service" {
  role       = aws_iam_role.glue_job.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSGlueServiceRole"
}

data "aws_iam_policy_document" "glue_job_buckets" {
  statement {
    sid     = "AllBucketsList"
    actions = ["s3:ListBucket"]
    resources = [
      aws_s3_bucket.data["raw"].arn,
      aws_s3_bucket.data["staging"].arn,
      aws_s3_bucket.data["curated"].arn,
      aws_s3_bucket.data["artifacts"].arn,
    ]
  }

  statement {
    sid     = "ReadOnlyZones"
    actions = ["s3:GetObject"]
    resources = [
      "${aws_s3_bucket.data["raw"].arn}/*",
      "${aws_s3_bucket.data["artifacts"].arn}/*",
    ]
  }

  statement {
    sid = "ReadWriteZones"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:AbortMultipartUpload",
    ]
    resources = [
      "${aws_s3_bucket.data["staging"].arn}/*",
      "${aws_s3_bucket.data["curated"].arn}/*",
    ]
  }
}

resource "aws_iam_role_policy" "glue_job_buckets" {
  name   = "${var.project_name}-glue-job-buckets"
  role   = aws_iam_role.glue_job.id
  policy = data.aws_iam_policy_document.glue_job_buckets.json
}

resource "aws_glue_job" "transform" {
  name              = "${var.project_name}-pyspark-transform"
  role_arn          = aws_iam_role.glue_job.arn
  glue_version      = "4.0"
  worker_type       = "G.1X"
  number_of_workers = 2
  max_retries       = 1
  timeout           = 15

  command {
    name            = "glueetl"
    script_location = "s3://${aws_s3_object.glue_pyspark_script.bucket}/${aws_s3_object.glue_pyspark_script.key}"
    python_version  = "3"
  }

  default_arguments = {
    "--enable-metrics"                   = ""
    "--enable-continuous-cloudwatch-log" = "true"
    # Glue 4.0 jobs default to a job-local Hive metastore, so SparkSession
    # would only see `default`. This flag swaps the metastore for the Glue
    # Data Catalog, which is where the Crawler registered `device_log.events`.
    "--enable-glue-datacatalog" = "true"
    "--sql_base_uri"            = "s3://${aws_s3_bucket.data["artifacts"].id}/sql/transforms"
    "--raw_database"            = aws_glue_catalog_database.zones["raw"].name
    "--staging_database"        = aws_glue_catalog_database.zones["staging"].name
    "--curated_database"        = aws_glue_catalog_database.zones["curated"].name
    "--staging_location"        = "s3://${aws_s3_bucket.data["staging"].id}"
    "--curated_location"        = "s3://${aws_s3_bucket.data["curated"].id}"
  }
}
