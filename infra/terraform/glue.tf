locals {
  # Glue database names can't contain hyphens; substitute the project-name
  # separator so `device-log` becomes `device_log` in the catalog.
  catalog_prefix = replace(var.project_name, "-", "_")
}

# One catalog database per zone, mirroring the three S3 zones. The
# PySpark wrapper writes each transform into the matching zone database,
# and the crawler populates `${catalog_prefix}_raw` from the raw bucket.
resource "aws_glue_catalog_database" "zones" {
  for_each = toset(["raw", "staging", "curated"])
  name     = "${local.catalog_prefix}_${each.key}"
}

data "aws_iam_policy_document" "glue_crawler_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["glue.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "glue_crawler" {
  name               = "${var.project_name}-glue-crawler"
  assume_role_policy = data.aws_iam_policy_document.glue_crawler_assume.json
}

resource "aws_iam_role_policy_attachment" "glue_crawler_service" {
  role       = aws_iam_role.glue_crawler.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSGlueServiceRole"
}

data "aws_iam_policy_document" "glue_crawler_raw_read" {
  statement {
    sid       = "RawListBucket"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.data["raw"].arn]
  }

  statement {
    sid       = "RawGetObject"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.data["raw"].arn}/*"]
  }
}

resource "aws_iam_role_policy" "glue_crawler_raw_read" {
  name   = "${var.project_name}-glue-crawler-raw-read"
  role   = aws_iam_role.glue_crawler.id
  policy = data.aws_iam_policy_document.glue_crawler_raw_read.json
}

resource "aws_glue_crawler" "raw" {
  name          = "${var.project_name}-raw"
  role          = aws_iam_role.glue_crawler.arn
  database_name = aws_glue_catalog_database.zones["raw"].name
  table_prefix  = ""

  s3_target {
    # Generators write under `events/dt=.../hour=.../*.parquet`. Pointing the
    # crawler at the `events/` prefix is what gives the discovered table its
    # predictable name (`events`); the dt/hour folders below become partitions.
    path = "s3://${aws_s3_bucket.data["raw"].id}/events/"
  }

  # `TableLevelConfiguration = 2` anchors the table at the `events/` prefix
  # (depth 2 from the bucket root: <bucket>/events/). Anything deeper —
  # `dt=…/hour=…/` — is interpreted as Hive partitions on the single table,
  # not as separate tables. Depth 3 splits one-table-per-dt, which is wrong.
  configuration = jsonencode({
    Version = 1.0
    CrawlerOutput = {
      Tables = { AddOrUpdateBehavior = "MergeNewColumns" }
    }
    Grouping = {
      TableGroupingPolicy     = "CombineCompatibleSchemas"
      TableLevelConfiguration = 2
    }
  })

  schema_change_policy {
    delete_behavior = "LOG"
    update_behavior = "UPDATE_IN_DATABASE"
  }
}
