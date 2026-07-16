import sys
import json
import csv
import logging
import google.auth
from datetime import datetime, timezone
from google.cloud import bigquery, dataplex_v1, storage
from google.protobuf.json_format import MessageToDict

# Configure logging format and level
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

logger.info("Initializing application and loading local configurations...")

# 1. Load config.json locally from the container deployment
try:
    with open("config.json", "r", encoding="utf-8") as f:
        config_data = json.load(f) or {}
    logger.info("Successfully loaded local config.json")
except Exception as e:
    logger.critical(f"CRITICAL: Could not load local config.json file. Error: {e}")
    sys.exit(1)

PROJECT_ID = config_data.get("PROJECT_ID", "vz-it-np-keiv-dev-dpev-0")
LOCATION = config_data.get("LOCATION", "us")
DQ_DATASET = config_data.get("DQ_DATASET", "gcp_governance_tbls")
DQ_TABLE = config_data.get("DQ_TABLE", "data_quality_results")
GOVERNANCE_ASPECT_TYPE = config_data.get("GOVERNANCE_ASPECT_TYPE")
TRUST_ASPECT_TYPE = config_data.get("TRUST_ASPECT_TYPE")

# Fetch GCS target routing info from the config
GCS_BUCKET_NAME = config_data.get("GCS_BUCKET_NAME")
GCS_FOLDER_PATH = config_data.get("GCS_FOLDER_PATH", "").strip("/")

if not GCS_BUCKET_NAME:
    logger.critical("CRITICAL: 'GCS_BUCKET_NAME' parameter is missing from config.json. Exiting.")
    sys.exit(1)

# Construct prefix path if a nested subdirectory is specified
BLOB_PREFIX = f"{GCS_FOLDER_PATH}/" if GCS_FOLDER_PATH else ""

logger.info("Initializing cloud clients (Storage, BigQuery, Dataplex)...")
storage_client = storage.Client()
bucket = storage_client.bucket(GCS_BUCKET_NAME)
bq_client = bigquery.Client(project=PROJECT_ID)
dataplex_client = dataplex_v1.CatalogServiceClient()

# GCS Blob Filenames
CSV_FILENAME = config_data.get("LOCAL_CSV_PATH", "table_list.csv")
JSON_FILENAME = config_data.get("LOCAL_JSON_PATH", "dq_thresholds.json")

# Build absolute blob targets relative to your nested subdirectory definition
GCS_CSV_PATH = CSV_FILENAME if "/" in CSV_FILENAME else f"{BLOB_PREFIX}{CSV_FILENAME}"
GCS_JSON_PATH = JSON_FILENAME if "/" in JSON_FILENAME else f"{BLOB_PREFIX}{JSON_FILENAME}"

def get_aspect_map_key(full_aspect_resource):
    """Converts 'projects/A/locations/B/aspectTypes/C' to the API map key 'A.B.C'"""
    if not full_aspect_resource:
        return ""
    parts = full_aspect_resource.split('/')
    if len(parts) == 6:
        return f"{parts[1]}.{parts[3]}.{parts[5]}"
    return full_aspect_resource

# Generate the correct map keys required by the Dataplex dictionary
TRUST_MAP_KEY = get_aspect_map_key(TRUST_ASPECT_TYPE)

def get_trust_level(score, domain, config):
    if score is None:
        return "Unknown"
       
    thresholds = config.get('domain_benchmarks', {}).get(domain) or config.get('domain_benchmarks', {}).get('default', {})
    if not thresholds or 'high' not in thresholds or 'medium' not in thresholds:
        return "Unknown"
       
    if score >= thresholds['high']: return "high"
    if score >= thresholds['medium']: return "medium"
    return "low"

def get_bq_entry_name(project, dataset, table):
    return f"projects/{project}/locations/{LOCATION}/entryGroups/@bigquery/entries/bigquery.googleapis.com/projects/{project}/datasets/{dataset}/tables/{table}"

def update_trust_scores():
    logger.info("=== STARTING TRUST SCORE UPDATE PROCESS ===")
    try:
        # 1. Read DQ Thresholds Config from GCS Subdirectory
        logger.info(f"Step 1: Loading DQ Thresholds from GCS blob: gs://{GCS_BUCKET_NAME}/{GCS_JSON_PATH}")
        threshold_blob = bucket.blob(GCS_JSON_PATH)
        dq_config = json.loads(threshold_blob.download_as_text(encoding="utf-8"))
        logger.info("Successfully loaded threshold definitions into memory.")

        # 2. Read CSV File from GCS Subdirectory
        logger.info(f"Step 2: Loading table entries from GCS blob: gs://{GCS_BUCKET_NAME}/{GCS_CSV_PATH}")
        csv_blob = bucket.blob(GCS_CSV_PATH)
        csv_content = csv_blob.download_as_text(encoding="utf-8-sig")
        
        # Parse CSV text directly from memory split lines
        reader = csv.DictReader(csv_content.splitlines())
        records = list(reader)
        logger.info(f"Successfully loaded {len(records)} metadata records from CSV.")

        # 3. Fetch all latest BQ scores at once
        logger.info(f"Step 3: Fetching all latest DQ scores from {PROJECT_ID}.{DQ_DATASET}.{DQ_TABLE}...")
        query = f"""
            SELECT
                project_id,
                dataset_id,
                table_name,
                overall_dq_score,
                execution_timestamp
            FROM (
              SELECT
                  data_source.table_project_id AS project_id,
                  data_source.dataset_id AS dataset_id,
                  data_source.table_id AS table_name,
                  job_quality_result.score AS overall_dq_score,
                  job_end_time AS execution_timestamp,
                  ROW_NUMBER() OVER(
                      PARTITION BY data_source.table_project_id, data_source.dataset_id, data_source.table_id
                      ORDER BY job_end_time DESC
                  ) as rn
              FROM `{PROJECT_ID}.{DQ_DATASET}.{DQ_TABLE}`
              WHERE data_source.table_id IS NOT NULL
            )
            WHERE rn = 1
        """
        bq_results = bq_client.query(query).result()
        logger.info(f"Query Result : {bq_results}")
       
        dq_memory_dict = {}
        for row in bq_results:
            key = f"{row.project_id}.{row.dataset_id}.{row.table_name}"
            dq_memory_dict[key] = {
                "score": row.overall_dq_score,
                "raw_timestamp": row.execution_timestamp
            }
        logger.info(f"Loaded {len(dq_memory_dict)} unique table scores into memory.")

        # 4. Iterate through dataset records and apply Aspects
        logger.info("Step 4: Iterating through dataset records to apply Trust Scores...")
        success_count = 0
        error_count = 0

        for index, row in enumerate(records):
            t_project = row.get("table_project_id")
            t_dataset = row.get("dataset_id")
            t_table = row.get("table_id")
           
            logger.info(f"--- Processing item {index + 1}/{len(records)}: {t_project}.{t_dataset}.{t_table} ---")

            if not all([t_project, t_dataset, t_table]):
                logger.warning(f"Row {index + 1} skipped: Missing required project, dataset, or table value column in CSV. Data: {row}")
                error_count += 1
                continue

            entry_name = get_bq_entry_name(t_project, t_dataset, t_table)
            dict_key = f"{t_project}.{t_dataset}.{t_table}"

            try:
                # 5. Fetch Dataplex Entry (Using ALL view mapping context)
                logger.info(f"Fetching Dataplex entry for {t_table}...")
                request_entry = dataplex_v1.GetEntryRequest(
                    name=entry_name,
                    view=dataplex_v1.EntryView.ALL
                )
                entry = dataplex_client.get_entry(request=request_entry)
               
                domain = "default"
                logger.info(f"Attached Aspect Keys found: {list(entry.aspects.keys())}")
               
                for aspect_map_key, aspect_obj in entry.aspects.items():
                    if "data-governance" in aspect_map_key.lower():
                        logger.info(f"Successfully located Governance metadata under key: {aspect_map_key}")
                       
                        try:
                            fields_dict = MessageToDict(aspect_obj._pb.data) if aspect_obj._pb.data else {}
                        except Exception as parse_err:
                            logger.warning(f"MessageToDict parsing fallback active. Error: {parse_err}")
                            fields_dict = {k: v for k, v in aspect_obj.data.items()}
                       
                        logger.info(f"Extracted Governance fields successfully: {fields_dict}")
                       
                        # Dynamically search the sanitized key contents
                        for field_key, field_value in fields_dict.items():
                            sanitized_key = field_key.lower().replace("-", "").replace("_", "").replace(" ", "")
                            if sanitized_key == "datadomain":
                                domain = field_value
                                logger.info(f"Found domain context '{domain}' associated with {t_table}.")
                                break
                        break

                if domain == "default":
                    logger.warning(f"Could not discover an assigned domain field inside Governance data for {t_table}. Using 'default'.")
               
                # 6. Dictionary lookup
                dq_data = dq_memory_dict.get(dict_key)
                if dq_data:
                    score = dq_data["score"]
                    last_evaluated = dq_data["raw_timestamp"].astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
                    logger.info(f"Found matching DQ result in memory: Score {score}, Last Evaluated {last_evaluated}.")
                else:
                    score = None
                    last_evaluated = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
                    logger.warning(f"No DQ result found in memory for {dict_key}. Score set to None.")

                # Calculate Tier
                trust_level = get_trust_level(score, domain, dq_config)
                logger.info(f"Calculated Trust Tier: {trust_level}")

                # 7. Apply Aspect
                logger.info(f"Applying Aspect '{TRUST_ASPECT_TYPE}' to entry...")
                new_aspect = dataplex_v1.Aspect(
                    aspect_type=TRUST_ASPECT_TYPE,
                    data={
                        "trust_score": trust_level,
                        "last_evaluated": last_evaluated
                    }
                )

                # Create an isolated Entry object container to update only our modified target key
                update_entry_obj = dataplex_v1.Entry()
                update_entry_obj.name = entry.name
                update_entry_obj.aspects[TRUST_MAP_KEY] = new_aspect
               
                request_update = dataplex_v1.UpdateEntryRequest(
                    entry=update_entry_obj,
                    update_mask={"paths": ["aspects"]},
                    aspect_keys=[TRUST_MAP_KEY]
                )
               
                dataplex_client.update_entry(request=request_update)
                logger.info(f"SUCCESS: Applied {trust_level} Trust Score to {t_table}.")
                success_count += 1

            except Exception as e:
                logger.exception(f"ERROR processing table {t_table}: {e}")
                error_count += 1

        logger.info(f"=== PROCESS COMPLETE. Successes: {success_count}, Errors: {error_count} ===")
       
        if error_count > 0:
            sys.exit(1)
        else:
            sys.exit(0)

    except Exception as e:
        logger.exception(f"CRITICAL ERROR executing trust score update: {e}")
        sys.exit(1)

if __name__ == "__main__":
    update_trust_scores()
    ###
