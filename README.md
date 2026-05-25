# Log Intelligence Pipeline

An end-to-end data pipeline built to simulate, ingest, and analyze high-volume IoT device logs. This project showcases a complete event-driven architecture: a custom local generator streams simulated device telemetry into AWS, where the data is validated, processed via serverless ETL, and visualized on an interactive Streamlit dashboard.

During the main testing phase, the pipeline successfully handled over 18.6 million raw records, catching anomalies, filtering duplicates, and routing malformed data to a Dead Letter Queue.

## Tech Stack

* **AWS:** Kinesis, Lambda, Glue (PySpark), Athena, Step Functions, SQS, S3
* **Infrastructure:** Terraform
* **Dashboard:** Streamlit
* **Languages:** Python, SQL

## Quick Start

Make sure you have AWS credentials configured, plus Terraform and `uv` installed.

1. **Deploy the AWS resources:**

   ```bash
   make demo-up
   ```

2. **Generate data:**

    ```bash
    make backfill   # Generate 7 days of historical events
    make stream     # Stream live events to Kinesis for 5 mins
    ```

3. **Run the ETL job:**

    ```bash
    make trigger-etl
    ```

4. **Launch the analytics UI:**

    ```bash
    make dashboard
    ```

5. **Clean up (to avoid AWS charges):**

    ```bash
    make demo-down-streaming
    ```

Available Commands

I added a Makefile to simplify infrastructure and pipeline management.

```bash
make help - Show all commands

make demo-up - Apply full Terraform stack

make demo-down-streaming - Destroy streaming resources only (saves money)

make build-lambda-layer - Build Lambda layer zip (Linux x86_64)

make crawler / make crawler-status - Start Glue crawler / Show its state

make trigger-etl / make etl-status - Start ETL Step Functions run / Show last 5 runs

make backfill - Generate 7 days of historical events to raw S3

make stream - Stream events to Kinesis for 5 minutes

make dashboard - Run Streamlit dashboard

make test - Run tests

make lint - Run ruff lint

make fmt - Format Python and Terraform code
```
