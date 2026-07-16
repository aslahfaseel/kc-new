import argparse
import csv
import logging
import os
import time
from google.cloud import dataplex_v1
from google.cloud import bigquery
from google.api_core.exceptions import AlreadyExists

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)


def build_insight_field_map(insight_fields):
    """Recursively builds a lookup map for column insights."""
    mapping = {}
    for f in insight_fields:
        mapping[f.name] = {
            'description': f.description,
            'fields': build_insight_field_map(f.fields) if f.fields else {}
        }
    return mapping


def update_schema_recursively(bq_fields, insight_map):
    """Recursively updates BQ schema fields if descriptions are missing."""
    updated_any = False
    new_schema = []

    for field in bq_fields:
        insight_info = insight_map.get(field.name)
        field_changed = False

        current_desc = field.description
        if not current_desc and insight_info and insight_info.get('description'):
            current_desc = insight_info['description']
            field_changed = True
            logger.info(f"    - Found insight for column: {field.name}")

        current_sub_fields = list(field.fields)
        if current_sub_fields and insight_info and insight_info.get('fields'):
            updated_sub, sub_changed = update_schema_recursively(current_sub_fields, insight_info['fields'])
            if sub_changed:
                current_sub_fields = updated_sub
                field_changed = True

        if field_changed:
            f_repr = field.to_api_repr()
            f_repr['description'] = current_desc
            if current_sub_fields:
                f_repr['fields'] = [f.to_api_repr() for f in current_sub_fields]
            new_schema.append(bigquery.SchemaField.from_api_repr(f_repr))
            updated_any = True
        else:
            new_schema.append(field)

    return new_schema, updated_any


def generate_and_publish_scan(dataplex_client, bq_client, project_id, location, dataset_id, table_id=None):
    """
    Creates and triggers a Dataplex Data Documentation scan.
    Works for BOTH datasets (if table_id=None) and tables.
    """
    parent = f"projects/{project_id}/locations/{location}"
    
    # 1. Determine resource type and paths
    if table_id:
        datascan_id = f"doc-scan-{dataset_id}-{table_id}".replace("_", "-")
        resource_path = f"//bigquery.googleapis.com/projects/{project_id}/datasets/{dataset_id}/tables/{table_id}"
        logger.info(f"Attaching Knowledge Catalog publishing labels to Table: {dataset_id}.{table_id}...")
        
        # Get and label BQ Table
        table_ref = bq_client.get_table(f"{project_id}.{dataset_id}.{table_id}")
        labels = table_ref.labels or {}
        labels.update({
            "dataplex-data-documentation-published-scan": datascan_id,
            "dataplex-data-documentation-published-project": project_id,
            "dataplex-data-documentation-published-location": location,
        })
        table_ref.labels = labels
        bq_client.update_table(table_ref, ["labels"])
    else:
        datascan_id = f"doc-scan-{dataset_id}".replace("_", "-")
        resource_path = f"//bigquery.googleapis.com/projects/{project_id}/datasets/{dataset_id}"
        logger.info(f"Attaching Knowledge Catalog publishing labels to Dataset: {dataset_id}...")
        
        # Get and label BQ Dataset
        dataset_ref = bq_client.get_dataset(f"{project_id}.{dataset_id}")
        labels = dataset_ref.labels or {}
        labels.update({
            "dataplex-data-documentation-published-scan": datascan_id,
            "dataplex-data-documentation-published-project": project_id,
            "dataplex-data-documentation-published-location": location,
        })
        dataset_ref.labels = labels
        bq_client.update_dataset(dataset_ref, ["labels"])

    datascan_path = f"{parent}/dataScans/{datascan_id}"

    # 2. Define the Dataplex DataScan Configuration
    data_source = dataplex_v1.DataSource(resource=resource_path)

    data_documentation_spec = dataplex_v1.DataDocumentationSpec(
        catalog_publishing_enabled=True
    )
    data_scan_config = dataplex_v1.DataScan(
        data=data_source,
        data_documentation_spec=data_documentation_spec,
        display_name=f"Doc Scan for {table_id if table_id else dataset_id}",
        description="Programmatically triggered Data Documentation Scan."
    )

    # 3. Create the DataScan Target
    try:
        logger.info(f"Creating Dataplex DataScan: {datascan_id}...")
        create_request = dataplex_v1.CreateDataScanRequest(
            parent=parent,
            data_scan=data_scan_config,
            data_scan_id=datascan_id
        )
        operation = dataplex_client.create_data_scan(request=create_request)
        operation.result()
        logger.info(f"DataScan {datascan_id} created successfully.")
    except AlreadyExists:
        logger.info(f"DataScan {datascan_id} already exists. Skipping creation.")

    # 4. Trigger and run the DataScan immediately
    logger.info(f"Triggering execution of documentation scan for {table_id if table_id else dataset_id}...")
    run_request = dataplex_v1.RunDataScanRequest(name=datascan_path)
    run_job = dataplex_client.run_data_scan(request=run_request)
    job_name = run_job.job.name
    logger.info(f"Job triggered! Job path: {job_name}")

    # 5. Poll the job until it succeeds
    logger.info("Waiting for insights generation to complete...")
    while True:
        job = dataplex_client.get_data_scan_job(
            request={
                "name": job_name,
                "view": dataplex_v1.types.GetDataScanJobRequest.DataScanJobView.FULL
            }
        )
        state = job.state
        if state == dataplex_v1.types.DataScanJob.State.SUCCEEDED:
            logger.info("Insights generation job succeeded!")
            return job
        elif state in (dataplex_v1.types.DataScanJob.State.FAILED, dataplex_v1.types.DataScanJob.State.CANCELLED):
            raise RuntimeError(f"Dataplex job failed or was cancelled with state: {state}")
        
        logger.info("Scan in progress... sleeping for 15 seconds.")
        time.sleep(15)


def process_dataset(bq_client, dataplex_client, project_id, dataset_id, location):
    """Generates, publishes, and writes insights to the BigQuery Dataset."""
    logger.info(f"=== [START] Dataset-Level Overview: {dataset_id} ===")

    job = generate_and_publish_scan(dataplex_client, bq_client, project_id, location, dataset_id)
    
    if (job and job.data_documentation_result and 
            job.data_documentation_result.dataset_result and 
            job.data_documentation_result.dataset_result.overview):
        
        ds_ref = bq_client.get_dataset(f"{project_id}.{dataset_id}")
        if not ds_ref.description:
            ds_ref.description = job.data_documentation_result.dataset_result.overview
            bq_client.update_dataset(ds_ref, ["description"])
            logger.info(f"Successfully updated BigQuery Dataset description for: {dataset_id}")
    else:
        logger.warning(f"No Dataset-level overview insights generated for dataset {dataset_id}.")


def process_table(bq_client, dataplex_client, project_id, dataset_id, table_id, location):
    """Generates, publishes, and synchronizes insights for a specific BigQuery table."""
    logger.info(f"=== [START] Table-Level Overview: {dataset_id}.{table_id} ===")

    job = generate_and_publish_scan(dataplex_client, bq_client, project_id, location, dataset_id, table_id)

    if not job or not job.data_documentation_result or not job.data_documentation_result.table_result:
        logger.warning(f"No successful Documentation results retrieved for table {table_id}.")
        return

    table_result = job.data_documentation_result.table_result
    table_ref = bq_client.get_table(f"{project_id}.{dataset_id}.{table_id}")
    table_changed = False

    # 1. Update Table Overview description
    if not table_ref.description and table_result.overview:
        table_ref.description = table_result.overview
        table_changed = True
        logger.info(f"  - Found table-level overview description.")

    # 2. Update Column Descriptions
    if table_result.schema and table_result.schema.fields:
        insight_map = build_insight_field_map(table_result.schema.fields)
        new_schema, schema_updated = update_schema_recursively(table_ref.schema, insight_map)
        if schema_updated:
            table_ref.schema = new_schema
            table_changed = True

    if table_changed:
        bq_client.update_table(table_ref, ["description", "schema"])
        logger.info(f"Successfully synchronized metadata into BigQuery table {table_id}.")
    else:
        logger.info(f"No schema updates required for table {table_id} (already up-to-date).")


def main():
    parser = argparse.ArgumentParser(description="Generate and populate BigQuery metadata from a CSV file.")
    parser.add_argument("--csv_path", default="data_insights.csv", help="Path to the CSV file (default: data_insights)")
    args = parser.parse_args()

    if not os.path.exists(args.csv_path):
        logger.error(f"CSV file not found at: {args.csv_path}")
        return

    dataplex_client = dataplex_v1.DataScanServiceClient()
    bq_clients = {}  # Cache BigQuery clients per project_id
    processed_datasets = set()  # Track (project_id, dataset_id, location) to run dataset scan only once

    logger.info(f"Reading target resources from CSV: {args.csv_path}")
    with open(args.csv_path, mode='r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row_idx, row in enumerate(reader, start=1):
            project_id = row.get('project_id', '').strip()
            location = row.get('location', '').strip()
            dataset_id = row.get('dataset_id', '').strip()
            table_id = row.get('table', '').strip()

            if not all([project_id, location, dataset_id]):
                logger.warning(f"Row {row_idx}: Missing required project_id, location, or dataset_id. Skipping.")
                continue

            # Get or initialize BQ client for this project
            if project_id not in bq_clients:
                bq_clients[project_id] = bigquery.Client(project=project_id)
            bq_client = bq_clients[project_id]

            # 1. Process Dataset-Level overview (only once per unique dataset)
            dataset_key = (project_id, dataset_id, location)
            if dataset_key not in processed_datasets:
                try:
                    process_dataset(bq_client, dataplex_client, project_id, dataset_id, location)
                except Exception as e:
                    logger.error(f"Failed to process dataset {dataset_id}: {e}")
                processed_datasets.add(dataset_key)

            # 2. Process Table-Level overview
            if table_id:
                try:
                    process_table(bq_client, dataplex_client, project_id, dataset_id, table_id, location)
                except Exception as e:
                    logger.error(f"Failed to process table {table_id}: {e}")
            else:
                logger.info(f"Row {row_idx}: No table specified. Dataset-level processing completed.")


if __name__ == "__main__":
    main()
