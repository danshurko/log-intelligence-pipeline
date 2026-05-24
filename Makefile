.PHONY: help build-lambda-layer demo-up demo-down-streaming \
        crawler crawler-status trigger-etl etl-status \
        backfill stream dashboard test fmt lint

.DEFAULT_GOAL := help

help:  ## Show this help
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z][a-zA-Z0-9_-]*:.*?## / {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

build-lambda-layer:  ## Build Lambda layer zip (Linux x86_64)
	rm -rf infra/terraform/build/layer
	mkdir -p infra/terraform/build/layer/python
	docker run --rm --platform linux/amd64 \
		-v $(PWD)/infra/terraform/build/layer/python:/asset-output \
		--entrypoint pip \
		public.ecr.aws/lambda/python:3.12 \
		install --target /asset-output pyarrow pydantic
	cd infra/terraform/build/layer && zip -qr ../lambda_layer.zip python

demo-up: build-lambda-layer  ## Apply full Terraform stack
	cd infra/terraform && terraform apply -auto-approve

demo-down-streaming:  ## Destroy streaming resources only
	cd infra/terraform && terraform destroy \
		-target=aws_lambda_event_source_mapping.kinesis_consumer \
		-target=aws_lambda_function.kinesis_consumer \
		-target=aws_kinesis_stream.device_logs \
		-auto-approve

crawler:  ## Start Glue crawler
	aws glue start-crawler --name $$(cd infra/terraform && terraform output -raw crawler_name)

crawler-status:  ## Show Glue crawler state
	aws glue get-crawler --name $$(cd infra/terraform && terraform output -raw crawler_name) --query 'Crawler.State'

trigger-etl:  ## Start ETL Step Functions run
	aws stepfunctions start-execution --state-machine-arn $$(cd infra/terraform && terraform output -raw state_machine_arn)

etl-status:  ## Show last 5 ETL runs
	aws stepfunctions list-executions --state-machine-arn $$(cd infra/terraform && terraform output -raw state_machine_arn) --max-items 5

backfill:  ## Generate 7 days of historical events to raw S3
	uv run python -m src.generator.cli backfill \
		--days 7 --devices 50 \
		--bucket $$(cd infra/terraform && terraform output -raw raw_bucket_name)

stream:  ## Stream events to Kinesis for 5 minutes
	uv run python -m src.generator.cli stream \
		--rate 10 --duration 300 --scenario normal \
		--stream-name $$(cd infra/terraform && terraform output -raw stream_name)

dashboard:  ## Run Streamlit dashboard
	@AWS_REGION=$$(cd infra/terraform && terraform output -raw aws_region) \
	 ATHENA_OUTPUT_S3="s3://$$(cd infra/terraform && terraform output -raw artifacts_bucket_name)/athena-results/" \
	 ATHENA_WORKGROUP=primary \
	 CURATED_DATABASE=device_log_curated \
	 DLQ_NAME=$$(cd infra/terraform && terraform output -raw dlq_name) \
	 STATE_MACHINE_ARN=$$(cd infra/terraform && terraform output -raw state_machine_arn) \
	 uv run streamlit run src/dashboard/app.py

test:  ## Run tests
	uv run pytest -q

lint:  ## Run ruff lint
	uv run ruff check src tests

fmt:  ## Format Python and Terraform
	uv run ruff format src tests
	cd infra/terraform && terraform fmt -recursive