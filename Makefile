# infra targets

build-lambda-layer:
	rm -rf infra/terraform/build/layer
	mkdir -p infra/terraform/build/layer/python
	docker run --rm --platform linux/amd64 \
		-v $(PWD)/infra/terraform/build/layer/python:/asset-output \
		--entrypoint pip \
		public.ecr.aws/lambda/python:3.12 \
		install --target /asset-output pyarrow pydantic
	cd infra/terraform/build/layer && zip -qr ../lambda_layer.zip python

demo-up: build-lambda-layer
	cd infra/terraform && terraform apply -auto-approve

demo-down-streaming:
	cd infra/terraform && terraform destroy \
		-target=aws_lambda_event_source_mapping.kinesis_consumer \
		-target=aws_lambda_function.kinesis_consumer \
		-target=aws_kinesis_stream.device_logs \
		-auto-approve

crawler:
	aws glue start-crawler --name $$(cd infra/terraform && terraform output -raw crawler_name)

crawler-status:
	aws glue get-crawler --name $$(cd infra/terraform && terraform output -raw crawler_name) --query 'Crawler.State'

trigger-etl:
	aws stepfunctions start-execution --state-machine-arn $$(cd infra/terraform && terraform output -raw state_machine_arn)

etl-status:
	aws stepfunctions list-executions --state-machine-arn $$(cd infra/terraform && terraform output -raw state_machine_arn) --max-items 5

# pipeline targets

backfill:
	uv run python -m src.generator.cli backfill \
		--days 7 --devices 50 \
		--bucket $$(cd infra/terraform && terraform output -raw raw_bucket_name)

stream:
	uv run python -m src.generator.cli stream \
		--rate 10 --duration 300 --scenario normal \
		--stream-name $$(cd infra/terraform && terraform output -raw stream_name)

# dev targets

dashboard:
	@AWS_REGION=$$(cd infra/terraform && terraform output -raw aws_region) \
	 ATHENA_OUTPUT_S3="s3://$$(cd infra/terraform && terraform output -raw artifacts_bucket_name)/athena-results/" \
	 ATHENA_WORKGROUP=primary \
	 CURATED_DATABASE=device_log_curated \
	 DLQ_NAME=$$(cd infra/terraform && terraform output -raw dlq_name) \
	 STATE_MACHINE_ARN=$$(cd infra/terraform && terraform output -raw state_machine_arn) \
	 uv run streamlit run src/dashboard/app.py
