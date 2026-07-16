import os
import sys
import re
import yaml
import json
import logging
import requests
import pandas as pd
import google.auth
import google.auth.transport.requests

from google.api_core.client_options import ClientOptions
from io import StringIO
from datetime import datetime
from google.auth.transport.requests import Request
from google.cloud import storage
from google.cloud import bigquery
from google.cloud import dataplex_v1
from google.protobuf import field_mask_pb2

# Logging Setup
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# Sample data structure matching pipeline requirements
SAMPLE_DATA = {
    "project_id": ["your-gcp-project-id"],
    "location": ["us-central1"],
    "entry_group": ["your-entry-group"],
    "dataset_id": ["your_dataset"],
    "asset_id": ["your_table"],
    "Status": ["PENDING"],
    "aspect_type_id": ["your_aspect_type"],
    "aspect_action": ["CREATE"],
    "additional_metadata_key": ["example_value"]
}


def create_sample_dataframe():
    """Generates a standardized sample DataFrame matching requirements."""
    return pd.DataFrame(SAMPLE_DATA)


REQUIRED_COLUMNS = [
    "project_id", "location", "entry_group", "dataset_id",
    "asset_id", "Status", "aspect_type_id", "aspect_action"
]


def load_config():
    config_path = os.path.join(os.path.dirname(__file__), 'config.yaml')
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)


def get_credentials():
    credentials, _ = google.auth.default(
        scopes=[
            "https://www.googleapis.com/auth/cloud-platform",
            "https://www.googleapis.com/auth/spreadsheets"
        ]
    )
    credentials.refresh(Request())
    return credentials


# ==========================================
# GCS LOGIC
# ==========================================
def find_and_read_gcs_csvs(bucket_name, prefix):
    """Finds all CSVs starting with the prefix and ending with _DD_MM_YYYY.csv for the current date."""
    storage_client = storage.Client()
    current_date_str = datetime.now().strftime("%d_%m_%Y")
    expected_suffix = f"_{current_date_str}.csv"

    logging.info(f"Searching for GCS blobs starting with '{prefix}' and ending with '{expected_suffix}'")
    blobs = list(storage_client.list_blobs(bucket_name, prefix=prefix))
    matched_blobs = [b for b in blobs if b.name.endswith(expected_suffix)]

    if not matched_blobs:
        logging.warning(f"No CSV files found for today's date matching pattern: {prefix}*{expected_suffix}")

    return matched_blobs, blobs


def upload_csv_to_gcs(bucket_name, blob_name, df):
    """Overwrites the GCS CSV with updated Status values."""
    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    blob.upload_from_string(df.to_csv(index=False), content_type='text/csv')
    logging.info(f"Updated source CSV on GCS: gs://{bucket_name}/{blob_name}")


# ==========================================
# GOOGLE SHEETS & DRIVE LOGIC
# ==========================================
def create_new_spreadsheet(title, token):
    """Creates a blank Google Spreadsheet and returns its ID."""
    url = "https://sheets.googleapis.com/v4/spreadsheets"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {"properties": {"title": title}}
    response = requests.post(url, headers=headers, json=payload)
    if response.status_code != 200:
        raise RuntimeError(f"Failed to create new spreadsheet: {response.text}")
    return response.json().get("spreadsheetId")


def share_spreadsheet_with_user(spreadsheet_id, user_email, token):
    """Uses the Google Drive API to share the spreadsheet with a specific user email."""
    url = f"https://www.googleapis.com/drive/v3/files/{spreadsheet_id}/permissions"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {
        "role": "writer",
        "type": "user",
        "emailAddress": user_email
    }
    logging.info(f"Drive API: Attempting to share sheet {spreadsheet_id} with {user_email}...")
    response = requests.post(url, headers=headers, json=payload)
    if response.status_code != 200:
        logging.error(f"Failed to share spreadsheet with user: {response.text}")
    else:
        logging.info(f"Successfully shared spreadsheet with {user_email}.")


def add_tab_to_spreadsheet(spreadsheet_id, tab_name, token):
    """Appends a new tab to an existing spreadsheet."""
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}:batchUpdate"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {
        "requests": [{
            "addSheet": {
                "properties": {"title": tab_name}
            }
        }]
    }
    response = requests.post(url, headers=headers, json=payload)
    if response.status_code != 200 and "already exists" not in response.text:
        raise RuntimeError(f"Failed to add tab '{tab_name}': {response.text}")


def write_dataframe_to_sheet(spreadsheet_id, tab_name, df, token):
    """Overwrites an entire tab's range with DataFrame contents (headers + data)."""
    header_row = list(df.columns)
    data_rows = df.astype(str).values.tolist()
    values = [header_row] + data_rows

    url = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values/{tab_name}!A1?valueInputOption=USER_ENTERED"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {"values": values}
    response = requests.put(url, headers=headers, json=payload)
    if response.status_code != 200:
        raise RuntimeError(f"Failed to populate data in tab '{tab_name}': {response.text}")


def extract_spreadsheet_id(url):
    match = re.search(r'/d/([a-zA-Z0-9-_]+)', url)
    if match:
        return match.group(1)
    raise ValueError("Could not find a valid Spreadsheet ID in the provided URL.")


def get_all_tabs(spreadsheet_id, token):
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}"
    headers = {"Authorization": f"Bearer {token}"}
    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        raise RuntimeError(f"Failed to fetch spreadsheet metadata: {response.text}")
    return [sheet['properties']['title'] for sheet in response.json().get('sheets', [])]


def read_gsheet_to_dataframe(spreadsheet_id, tab_name, token):
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values/{tab_name}?valueRenderOption=UNFORMATTED_VALUE"
    headers = {"Authorization": f"Bearer {token}"}
    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        return None
    data = response.json().get('values', [])
    if not data or len(data) < 2:
        return None
    return pd.DataFrame(data[1:], columns=data[0])


def col_num_to_letter(n):
    string = ""
    while n >= 0:
        string = chr(n % 26 + 65) + string
        n = n // 26 - 1
    return string


def update_sheet_status(spreadsheet_id, tab_name, cell_range, status_msg, token):
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values/{tab_name}!{cell_range}?valueInputOption=USER_ENTERED"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {"values": [[status_msg]]}
    requests.put(url, headers=headers, json=payload)


# ==========================================
# DATAPLEX API LOGIC
# ==========================================
def clean_payload(item_dict):
    keys_to_remove = set(REQUIRED_COLUMNS)
    return {k: v for k, v in item_dict.items() if k not in keys_to_remove and pd.notna(v) and v != ""}


def execute_dataplex_action(dataplex_client, action, project_id, location, entry_group, dataset_id, aspect_type_id,
                            payload, table_id=None):
    """Executes Dataplex metadata modifications using a shared client instance."""
    entry_id = f"bigquery.googleapis.com/projects/{project_id}/datasets/{dataset_id}"
    if table_id:
        entry_id += f"/tables/{table_id}"

    entry_name = f"projects/{project_id}/locations/{location}/entryGroups/{entry_group}/entries/{entry_id}"
    action = action.strip().upper()

    try:
        if action in ["CREATE", "UPDATE"]:
            aspect_key = f"{project_id}.{location}.{aspect_type_id}"
            aspect_instance = dataplex_v1.Aspect(data=payload)

            request = dataplex_v1.UpdateEntryRequest(
                entry=dataplex_v1.Entry(
                    name=entry_name,
                    aspects={aspect_key: aspect_instance}
                ),
                update_mask=field_mask_pb2.FieldMask(paths=["aspects"])
            )
            logging.info(f"SDK: Applying aspect data to {entry_id}")
            dataplex_client.update_entry(request=request)
            return 200

        elif action == "DELETE":
            logging.info(f"SDK: Starting targeted aspect unbind workflow for {entry_id}")
            aspect_key = f"{project_id}.{location}.{aspect_type_id}"

            request = dataplex_v1.UpdateEntryRequest(
                entry=dataplex_v1.Entry(
                    name=entry_name,
                    aspects={}
                ),
                update_mask=field_mask_pb2.FieldMask(paths=["aspects"]),
                aspect_keys=[aspect_key],
                delete_missing_aspects=True
            )

            logging.info(f"SDK: Dispatching targeted UpdateEntryRequest for key: {aspect_key}")
            dataplex_client.update_entry(request=request)
            return 200

    except Exception as e:
        logging.error(f"Dataplex SDK Error during {action} execution: {str(e)}")
        if "404" in str(e) or "Not found" in str(e):
            return 404
        return 500


# ==========================================
# BIGQUERY ROUTING EXPANSIONS
# ==========================================
def get_all_datasets(bq_client, project_id):
    return [ds.dataset_id for ds in bq_client.list_datasets(project_id)]


def get_all_tables_in_dataset(bq_client, project_id, dataset_id):
    try:
        dataset_ref = f"{project_id}.{dataset_id}"
        return [table.table_id for table in bq_client.list_tables(dataset_ref)]
    except Exception:
        return []


def parse_comma_list(val):
    if pd.isna(val) or str(val).strip() == "":
        return []
    return [v.strip() for v in str(val).split(",") if v.strip()]


# ==========================================
# PIPELINE ENGINES
# ==========================================
def process_dataframe(df, bq_client, dataplex_client, update_status_callback):
    """Processes DataFrame according to validation rules using persistent clients."""
    df_dict = df.to_dict("records")
    row_failures = []

    for idx, item in enumerate(df_dict):
        row_num = idx + 2

        if str(item.get("Status", "")).strip().upper() in ["DONE", "SUCCESS"]:
            logging.info(f"Row {row_num}: Already processed. Skipping.")
            continue

        if not all(pd.notna(item.get(col)) and str(item.get(col, "")).strip() != "" for col in
                   ["project_id", "location", "entry_group", "dataset_id", "aspect_type_id", "aspect_action"]):
            logging.warning(f"Row {row_num}: Missing primary identification coordinates. Skipping.")
            continue

        project_id = str(item.get("project_id")).strip()
        location = str(item.get("location")).strip()
        entry_group = str(item.get("entry_group")).strip()
        aspect_type_id = str(item.get("aspect_type_id")).strip()
        action = str(item.get("aspect_action")).strip().upper()
        payload = clean_payload(item)

        raw_dataset = str(item.get("dataset_id")).strip()
        raw_asset = str(item.get("asset_id", "")).strip() if pd.notna(item.get("asset_id")) else ""

        targets = []

        # Target Matrix Evaluations
        if raw_dataset == "*" and not raw_asset:
            for ds in get_all_datasets(bq_client, project_id):
                targets.append((ds, None))
        elif raw_dataset == "*" and raw_asset == "*":
            for ds in get_all_datasets(bq_client, project_id):
                for tb in get_all_tables_in_dataset(bq_client, project_id, ds):
                    targets.append((ds, tb))
        elif "," in raw_dataset and raw_asset == "*":
            for ds in parse_comma_list(raw_dataset):
                for tb in get_all_tables_in_dataset(bq_client, project_id, ds):
                    targets.append((ds, tb))
        elif "," in raw_dataset and not raw_asset:
            for ds in parse_comma_list(raw_dataset):
                targets.append((ds, None))
        elif "," not in raw_dataset and raw_dataset != "*" and raw_asset == "*":
            for tb in get_all_tables_in_dataset(bq_client, project_id, raw_dataset):
                targets.append((raw_dataset, tb))
        elif "," in raw_dataset and "," in raw_asset:
            datasets = parse_comma_list(raw_dataset)
            assets = parse_comma_list(raw_asset)
            for ds in datasets:
                valid_tables = get_all_tables_in_dataset(bq_client, project_id, ds)
                for asset in assets:
                    if asset in valid_tables:
                        targets.append((ds, asset))
        elif "," not in raw_dataset and raw_dataset != "*" and "," in raw_asset:
            for asset in parse_comma_list(raw_asset):
                targets.append((raw_dataset, asset))
        elif raw_dataset != "*" and raw_asset != "*" and "," not in raw_dataset and "," not in raw_asset and raw_asset != "":
            targets.append((raw_dataset, raw_asset))
        else:
            targets.append((raw_dataset, raw_asset if raw_asset else None))

        if not targets:
            logging.warning(f"Row {row_num}: Evaluated target matrix is completely empty.")
            update_status_callback(idx, "FAILED: No Targets Found")
            row_failures.append(row_num)
            continue

        row_has_errors = False
        for dataset_id, table_id in targets:
            try:
                status = execute_dataplex_action(dataplex_client, action, project_id, location, entry_group, dataset_id,
                                                 aspect_type_id, payload, table_id)
                if not str(status).startswith("2"):
                    logging.error(
                        f"Row {row_num}: Target execution failed for {dataset_id}{f'.{table_id}' if table_id else ''} with status code {status}")
                    row_has_errors = True
            except Exception as e:
                logging.error(f"Row {row_num}: Network or API error running targets: {str(e)}")
                row_has_errors = True

        if row_has_errors:
            update_status_callback(idx, "FAILED")
            row_failures.append(row_num)
        else:
            update_status_callback(idx, "DONE")

    return row_failures


# ==========================================
# MAIN EXECUTION ORCHESTRATOR
# ==========================================
def main():
    config = load_config()
    credentials = get_credentials()

    # from google.cloud.dataplex_v1.services.catalog_service.transports.grpc import CatalogServiceGrpcTransport
    #
    # # Define low-level channel options to increase metadata and message bounds
    # custom_grpc_options = [
    #     ('grpc.max_metadata_size', 64 * 1024),  # Expands metadata limit to 64KB (fixes the 16KB limit)
    #     ('grpc.max_receive_message_length', 32 * 1024 * 1024),  # 32MB safety padding
    #     ('grpc.max_send_message_length', 32 * 1024 * 1024)  # 32MB safety padding
    # ]
    #
    # # Explicitly build the gRPC transport layer with your custom options
    # dataplex_transport = CatalogServiceGrpcTransport(
    #     credentials=credentials,
    #     options=custom_grpc_options
    # )

    # Initialize Persistent Clients Once
    bq_client = bigquery.Client(credentials=credentials)
    dataplex_client = dataplex_v1.CatalogServiceClient(credentials=credentials)

    source_type = config.get("source_type", "gcs").strip().lower()
    global_pipeline_failed = False

    # ----------------------------------------
    # PROCESS FLOW: GCS BUCKET CSVs
    # ----------------------------------------
    if source_type == "gcs":
        gcs_conf = config.get('gcs', {})
        bucket_name = gcs_conf.get('bucket_name')
        prefix = gcs_conf.get('file_prefix')

        matched_blobs, all_blobs = find_and_read_gcs_csvs(bucket_name, prefix)
        should_create_sample = False
        current_date_str = datetime.now().strftime("%d_%m_%Y")
        sample_blob_name = f"{prefix}_{current_date_str}.csv"

        if not all_blobs or not matched_blobs:
            logging.info("Valid or matching GCS data not found. Triggering sample CSV creation.")
            should_create_sample = True
        else:
            valid_file_found = False
            for blob in matched_blobs:
                try:
                    content = blob.download_as_text()
                    test_df = pd.read_csv(StringIO(content))
                    missing_cols = [col for col in REQUIRED_COLUMNS if col not in test_df.columns]
                    if not missing_cols and len(test_df) >= 1:
                        valid_file_found = True
                        break
                except Exception:
                    continue
            if not valid_file_found:
                should_create_sample = True

        if should_create_sample:
            sample_df = create_sample_dataframe()
            upload_csv_to_gcs(bucket_name, sample_blob_name, sample_df)
            matched_blobs, _ = find_and_read_gcs_csvs(bucket_name, prefix)

        for blob in matched_blobs:
            logging.info(f"--- Processing GCS CSV: {blob.name} ---")
            content = blob.download_as_text()
            df = pd.read_csv(StringIO(content))

            missing_cols = [col for col in REQUIRED_COLUMNS if col not in df.columns]
            if missing_cols:
                logging.error(f"CSV {blob.name} missing essential schema elements. Skipping.")
                global_pipeline_failed = True
                continue

            def csv_status_update(row_idx, status_msg):
                df.at[row_idx, "Status"] = status_msg

            failed_rows = process_dataframe(df, bq_client, dataplex_client, csv_status_update)
            upload_csv_to_gcs(bucket_name, blob.name, df)

            if failed_rows:
                global_pipeline_failed = True

    # ----------------------------------------
    # PROCESS FLOW: GOOGLE SHEETS
    # ----------------------------------------
    elif source_type == "gsheet":
        gsheet_config = config.get('gsheets', {})
        sheet_url = gsheet_config.get('url')
        user_email = gsheet_config.get('user_email')

        if not sheet_url or str(sheet_url).strip() == "":
            logging.info("Google Sheet URL is missing in configuration. Creating new spreadsheet...")
            new_id = create_new_spreadsheet("Dataplex_Ingestion_Sample", credentials.token)
            sheet_url = f"https://docs.google.com/spreadsheets/d/{new_id}/edit#gid=0"

            # Share the newly generated sheet with your account details from config
            if user_email and str(user_email).strip() != "":
                share_spreadsheet_with_user(new_id, str(user_email).strip(), credentials.token)
            else:
                logging.warning("No 'user_email' found in config.yaml. Sheet remains accessible ONLY to SA.")

            sample_df = create_sample_dataframe()
            write_dataframe_to_sheet(new_id, "Sheet1", sample_df, credentials.token)

            if 'gsheets' not in config:
                config['gsheets'] = {}
            config['gsheets']['url'] = sheet_url
            config_path = os.path.join(os.path.dirname(__file__), 'config.yaml')
            with open(config_path, 'w') as f:
                yaml.safe_dump(config, f)

            logging.info(f"Successfully generated new Google Sheet. Link: {sheet_url}")

        spreadsheet_id = extract_spreadsheet_id(sheet_url)
        tab_names = get_all_tabs(spreadsheet_id, credentials.token)

        has_valid_tab = False
        for tab_name in tab_names:
            df = read_gsheet_to_dataframe(spreadsheet_id, tab_name, credentials.token)
            if df is not None and not df.empty:
                missing_cols = [col for col in REQUIRED_COLUMNS if col not in list(df.columns)]
                if not missing_cols and len(df) >= 1:
                    has_valid_tab = True
                    break

        if not has_valid_tab:
            sample_tab_name = "Sample_Input"
            add_tab_to_spreadsheet(spreadsheet_id, sample_tab_name, credentials.token)
            sample_df = create_sample_dataframe()
            write_dataframe_to_sheet(spreadsheet_id, sample_tab_name, sample_df, credentials.token)
            tab_names = get_all_tabs(spreadsheet_id, credentials.token)

        for tab_name in tab_names:
            logging.info(f"--- Processing Tab: {tab_name} ---")
            df = read_gsheet_to_dataframe(spreadsheet_id, tab_name, credentials.token)

            if df is None or df.empty:
                continue

            columns = list(df.columns)
            missing_cols = [col for col in REQUIRED_COLUMNS if col not in columns]
            if missing_cols:
                continue

            status_col_letter = col_num_to_letter(columns.index("Status"))

            def sheet_status_update(row_idx, status_msg):
                sheet_row_num = row_idx + 2
                cell_range = f"{status_col_letter}{sheet_row_num}"
                update_sheet_status(spreadsheet_id, tab_name, cell_range, status_msg, credentials.token)

            failed_rows = process_dataframe(df, bq_client, dataplex_client, sheet_status_update)
            if failed_rows:
                global_pipeline_failed = True

    # ----------------------------------------
    # PROCESS FLOW: BIGQUERY TABLE SOURCE
    # ----------------------------------------
    elif source_type == "bigquery":
        # 1. Fetch input/output details from config.yaml
        bq_conf = config.get('bigquery', {})
        bq_in = bq_conf.get('input', {})
        bq_out = bq_conf.get('output', {})

        in_project = bq_in.get('project')
        in_dataset = bq_in.get('dataset')
        in_table = bq_in.get('table')

        out_project = bq_out.get('project')
        out_dataset = bq_out.get('dataset')
        out_table = bq_out.get('table')

        if not all([in_project, in_dataset, in_table, out_project, out_dataset, out_table]):
            raise ValueError("BigQuery configuration paths for both 'input' and 'output' tables must be completely defined.")

        input_table_ref = f"{in_project}.{in_dataset}.{in_table}"
        output_table_ref = f"{out_project}.{out_dataset}.{out_table}"

        logging.info(f"--- Processing BigQuery Ingestion Source Table: {input_table_ref} ---")

        # 2 & 3. Fetch uncompleted framework rows (filtering out records already flagged as DONE/SUCCESS)
        query = f"""
            SELECT * FROM `{input_table_ref}`
            WHERE UPPER(TRIM(Status)) NOT IN ('DONE', 'SUCCESS') OR Status IS NULL
        """
        try:
            df = bq_client.query(query).to_dataframe()
        except Exception as e:
            logging.error(f"Failed to query records from input metadata table: {str(e)}")
            raise e

        if df.empty:
            logging.info("No records needing processing found inside the BigQuery input metadata table.")
        else:
            missing_cols = [col for col in REQUIRED_COLUMNS if col not in df.columns]
            if missing_cols:
                logging.error(f"BigQuery source table {input_table_ref} missing required framework elements: {missing_cols}")
                global_pipeline_failed = True
            else:
                # 4. Stream isolated execution telemetry parameters row-by-row into an output log table
                def bq_row_logger_callback(row_idx, status_msg):
                    row_data = df.iloc[row_idx].to_dict()
                    payload_columns_dict = clean_payload(row_data)
                    log_entry = {
                        "execution_timestamp": datetime.utcnow().isoformat(),
                        "project_id": str(row_data.get("project_id", "")),
                        "location": str(row_data.get("location", "")),
                        "entry_group": str(row_data.get("entry_group", "")),
                        "dataset_id": str(row_data.get("dataset_id", "")),
                        "asset_id": str(row_data.get("asset_id", "")),
                        "aspect_type_id": str(row_data.get("aspect_type_id", "")),
                        "aspect_action": str(row_data.get("aspect_action", "")),
                        "status": status_msg,
                        "aspect_payload": json.dumps(payload_columns_dict)
                    }
                    try:
                        hist_maintain_errors = bq_client.insert_rows_json(output_table_ref, [log_entry])
                        if not hist_maintain_errors:
                            logging.info(f"Streaming execution trace logged directly into BigQuery: {output_table_ref}")
                        else:
                            logging.error(f"Failed to stream row execution record to BigQuery output: {hist_maintain_errors}")
                    except Exception as le:
                        logging.error(f"Failed to stream row execution record to BigQuery output: {str(le)}")

                failed_rows = process_dataframe(df, bq_client, dataplex_client, bq_row_logger_callback)
                if failed_rows:
                    global_pipeline_failed = True
    else:
        raise ValueError(f"Unknown source_type '{source_type}' listed in config.yaml.")

    if global_pipeline_failed:
        raise RuntimeError("Metadata ingestion pipeline completed with failures targeting assets.")

    logging.info("Metadata deployment orchestrator executed perfectly without errors.")


if __name__ == "__main__":
    try:
        main()
    except Exception as err:
        print(json.dumps({"message": str(err), "severity": "ERROR"}))
        sys.exit(1)
