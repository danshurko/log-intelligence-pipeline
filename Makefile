# infra targets

# pipeline targets

backfill:
	uv run python -m src.generator.cli backfill \
		--days 7 --devices 50 \
		--bucket $$(cd infra/terraform && terraform output -raw raw_bucket_name)

# dev targets
