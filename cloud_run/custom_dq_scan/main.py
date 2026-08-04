import os
import sys
import re
import yaml
import json
import logging
import pandas as pd
import google.auth
from google.auth import impersonated_credentials
import google.auth.transport.requests
from io import StringIO
from datetime import datetime, timezone
from google.auth.transport.requests import Request
from google.cloud import storage
from google.cloud import bigquery
from google.api_core.exceptions import PermissionDenied, NotFound, GoogleAPICallError
import google.auth.transport.requests
import requests as http_requests

# ──────────────────────────────────────────────────────────────────────────────
# LOGGING
# ──────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# ──────────────────────────────────────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────────────────────────────────────
def load_config():
    config_path = os.path.join(os.path.dirname(__file__), "config.yaml")
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


# ──────────────────────────────────────────────────────────────────────────────
# CREDENTIALS
# ──────────────────────────────────────────────────────────────────────────────
def get_credentials(config):
    """
    Returns refreshed credentials. Supports optional SA impersonation when
    'impersonation.target_sa' is set in config.yaml.
    """
    source_credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )

    impersonate_conf = config.get("impersonation", {})
    target_sa = impersonate_conf.get("target_sa", "").strip()

    if target_sa:
        logging.info(f"Impersonating service account: {target_sa}")
        credentials = impersonated_credentials.Credentials(
            source_credentials=source_credentials,
            target_principal=target_sa,
            target_scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
    else:
        credentials = source_credentials

    credentials.refresh(Request())
    return credentials


# ──────────────────────────────────────────────────────────────────────────────
# GCS HELPERS
# ──────────────────────────────────────────────────────────────────────────────
def read_csv_from_gcs(bucket_name: str, blob_name: str) -> pd.DataFrame:
    """Downloads a CSV from GCS and returns it as a DataFrame."""
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    content = blob.download_as_text()
    df = pd.read_csv(StringIO(content), dtype=str) # Force all string to avoid float64 cast on empty status
    logging.info(f"Read {len(df)} rows from gs://{bucket_name}/{blob_name}")
    return df


def upload_csv_to_gcs(bucket_name: str, blob_name: str, df: pd.DataFrame):
    """Uploads a DataFrame as a CSV to GCS (overwrites)."""
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    blob.upload_from_string(df.to_csv(index=False), content_type="text/csv")
    logging.info(f"Written updated CSV back to gs://{bucket_name}/{blob_name}")


# ──────────────────────────────────────────────────────────────────────────────
# RULE LIBRARY  (mirrors custom_rules.tf + prebuilt_rules.tf)
# ──────────────────────────────────────────────────────────────────────────────
# Each entry is a dict that maps directly to a Dataplex DQ rule spec.
# 'type' drives which rule body block is used when building the API payload.
# Supported types:
#   row_condition | table_condition | sql_assertion |
#   non_null | uniqueness | range | regex | set | statistic_range

RULE_LIBRARY: dict = {
    # ── COMPLETENESS ──────────────────────────────────────────────────────────
    "raw-table-has-data": {
        "name": "raw-table-has-data",
        "dimension": "COMPLETENESS",
        "type": "table_condition",
        "sqlExpression": "COUNT(*) > 0",
    },
    "minimum-1000-rows": {
        "name": "minimum-1000-rows",
        "dimension": "COMPLETENESS",
        "type": "table_condition",
        "sqlExpression": "COUNT(*) >= 1000",
    },
    "id-not-null": {
        "name": "id-not-null",
        "dimension": "COMPLETENESS",
        "column": "id",
        "type": "row_condition",
        "sqlExpression": "id IS NOT NULL",
    },
    "customer-id-not-null": {
        "name": "customer-id-not-null",
        "dimension": "COMPLETENESS",
        "column": "customer_id",
        "type": "non_null",
        "threshold": 1.0,
    },
    "email-not-null": {
        "name": "email-not-null",
        "dimension": "COMPLETENESS",
        "column": "email",
        "type": "non_null",
        "threshold": 0.95,
    },
    # ── UNIQUENESS ───────────────────────────────────────────────────────────
    "customer-id-unique": {
        "name": "customer-id-unique",
        "dimension": "UNIQUENESS",
        "column": "customer_id",
        "type": "uniqueness",
        "threshold": 1.0,
    },
    "order-id-unique": {
        "name": "order-id-unique",
        "dimension": "UNIQUENESS",
        "column": "order_id",
        "type": "uniqueness",
        "threshold": 1.0,
    },
    # ── CONSISTENCY ──────────────────────────────────────────────────────────
    "end-date-after-start": {
        "name": "end-date-after-start",
        "dimension": "CONSISTENCY",
        "type": "row_condition",
        "sqlExpression": "end_date > start_date",
    },
    "discount-less-than-price": {
        "name": "discount-less-than-price",
        "dimension": "CONSISTENCY",
        "type": "row_condition",
        "sqlExpression": "discount_amount <= unit_price",
    },
    # ── TIMELINESS ───────────────────────────────────────────────────────────
    "no-future-created-date": {
        "name": "no-future-created-date",
        "dimension": "TIMELINESS",
        "type": "row_condition",
        "sqlExpression": "created_at <= CURRENT_TIMESTAMP()",
    },
    "data-freshness-daily": {
        "name": "data-freshness-daily",
        "dimension": "TIMELINESS",
        "type": "row_condition",
        "sqlExpression": "DATE(created_at) >= DATE_SUB(CURRENT_DATE(), INTERVAL 2 DAY)",
    },
    # ── VALIDITY ─────────────────────────────────────────────────────────────
    "amount-positive": {
        "name": "amount-positive",
        "dimension": "VALIDITY",
        "column": "amount",
        "type": "row_condition",
        "sqlExpression": "amount > 0",
    },
    "age-valid-range": {
        "name": "age-valid-range",
        "dimension": "VALIDITY",
        "column": "age",
        "type": "range",
        "threshold": 1.0,
        "minValue": "18",
        "maxValue": "120",
        "strictMinEnabled": False,
        "strictMaxEnabled": False,
    },
    "revenue-non-negative": {
        "name": "revenue-non-negative",
        "dimension": "VALIDITY",
        "column": "revenue",
        "type": "range",
        "threshold": 1.0,
        "minValue": "0",
        "maxValue": None,
        "strictMinEnabled": False,
        "strictMaxEnabled": False,
    },
    "order-status-valid": {
        "name": "order-status-valid",
        "dimension": "VALIDITY",
        "column": "status",
        "type": "set",
        "threshold": 1.0,
        "values": ["PENDING", "PROCESSING", "SHIPPED", "DELIVERED", "CANCELLED"],
    },
    "email-format-valid": {
        "name": "email-format-valid",
        "dimension": "VALIDITY",
        "column": "email",
        "type": "regex",
        "threshold": 0.99,
        "regex": r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$",
    },
}


# ──────────────────────────────────────────────────────────────────────────────
# RULE → API PAYLOAD BUILDER
# ──────────────────────────────────────────────────────────────────────────────
def build_rule_payload(rule: dict) -> dict:
    """
    Converts a RULE_LIBRARY entry into the Dataplex REST API rule format.
    Reference: https://cloud.google.com/dataplex/docs/reference/rest/v1/projects.locations.dataScans
    """
    rtype = rule.get("type", "")
    payload: dict = {"name": rule["name"], "dimension": rule["dimension"]}

    if rule.get("column"):
        payload["column"] = rule["column"]
    if rule.get("description"):
        payload["description"] = rule["description"]

    # threshold is excluded from table_condition & sql_assertion per Dataplex API
    if rtype not in ("table_condition", "sql_assertion"):
        payload["threshold"] = rule.get("threshold", 1.0)

    if rule.get("ignoreNull") is not None:
        payload["ignoreNull"] = rule["ignoreNull"]

    if rtype == "row_condition":
        payload["rowConditionExpectation"] = {"sqlExpression": rule["sqlExpression"]}
    elif rtype == "table_condition":
        payload["tableConditionExpectation"] = {"sqlExpression": rule["sqlExpression"]}
    elif rtype == "sql_assertion":
        payload["sqlAssertion"] = {"sqlStatement": rule["sqlStatement"]}
    elif rtype == "non_null":
        payload["nonNullExpectation"] = {}
    elif rtype == "uniqueness":
        payload["uniquenessExpectation"] = {}
    elif rtype == "range":
        re_block: dict = {}
        if rule.get("minValue") is not None:
            re_block["minValue"] = rule["minValue"]
        if rule.get("maxValue") is not None:
            re_block["maxValue"] = rule["maxValue"]
        re_block["strictMinEnabled"] = rule.get("strictMinEnabled", False)
        re_block["strictMaxEnabled"] = rule.get("strictMaxEnabled", False)
        payload["rangeExpectation"] = re_block
    elif rtype == "regex":
        payload["regexExpectation"] = {"regex": rule["regex"]}
    elif rtype == "set":
        payload["setExpectation"] = {"values": rule["values"]}
    elif rtype == "statistic_range":
        payload["statisticRangeExpectation"] = {
            "statistic": rule["statistic"],
            "minValue": rule.get("minValue"),
            "maxValue": rule.get("maxValue"),
            "strictMinEnabled": rule.get("strictMinEnabled", False),
            "strictMaxEnabled": rule.get("strictMaxEnabled", False),
        }
    else:
        raise ValueError(f"Unknown rule type '{rtype}' for rule '{rule['name']}'")

    return payload


# ──────────────────────────────────────────────────────────────────────────────
# DATAPLEX REST API  – CREATE / UPDATE DQ SCAN
# ──────────────────────────────────────────────────────────────────────────────
def _get_token(credentials) -> str:
    """Returns a fresh bearer token from the credentials object."""
    auth_req = google.auth.transport.requests.Request()
    credentials.refresh(auth_req)
    return credentials.token


def _scan_exists(project_id: str, location: str, scan_id: str, token: str) -> bool:
    """Returns True if the Dataplex scan already exists."""
    url = (
        f"https://dataplex.googleapis.com/v1/projects/{project_id}"
        f"/locations/{location}/dataScans/{scan_id}"
    )
    resp = http_requests.get(url, headers={"Authorization": f"Bearer {token}"})
    return resp.status_code == 200


def _build_scan_body(row: dict, rules: list[dict], config: dict) -> dict:
    """Builds the full Dataplex DQ scan REST body from a CSV row + resolved rules."""
    project_id = row["project_id"].strip()
    dataset = row["dataset"].strip()
    table_name = row["table_name"].strip()
    location = row.get("location", config["dataplex"]["default_location"]).strip()

    bq_resource = (
        f"//bigquery.googleapis.com/projects/{project_id}"
        f"/datasets/{dataset}/tables/{table_name}"
    )
    scan_id = (
        f"{dataset.replace('_', '-')}-{table_name.replace('_', '-')}-custom-dq"
    )
    display_name = (
        f"{project_id} - {dataset} - {table_name.replace('_', ' ').title()} - custom dq scan"
    )

    # Execution trigger
    schedule_cron = str(row.get("schedule_cron", "")).strip()
    if schedule_cron and schedule_cron.lower() != "nan":
        trigger = {"schedule": {"cron": schedule_cron}}
    else:
        trigger = {"onDemand": {}}

    # Sampling / row filter
    try:
        sampling = float(str(row.get("sampling_percent", "100")).strip() or "100")
    except ValueError:
        sampling = 100.0

    row_filter = str(row.get("row_filter", "")).strip()
    if not row_filter or row_filter.lower() == "nan":
        row_filter = None

    dq_spec: dict = {
        "samplingPercent": sampling,
        "catalogPublishingEnabled": True,
        "rules": rules,
        "postScanActions": {
            "bigqueryExport": {
                "resultsTable": config["dataplex"]["results_bq_table"]
            }
        },
    }
    if row_filter:
        dq_spec["rowFilter"] = row_filter

    body = {
        "displayName": display_name,
        "description": f"Custom DQ scan for {dataset}.{table_name}",
        "labels": {
            "project": "vz",
            "managed_by": "custom-dq-cloud-run",
            "scan_type": "dq-custom",
        },
        "data": {"resource": bq_resource},
        "executionSpec": {"trigger": trigger},
        "executionIdentity": {
            "serviceAccount": {
                "email": config["dataplex"]["service_account_email"]
            }
        },
        "dataQualitySpec": dq_spec,
    }
    return scan_id, body


def create_or_update_scan(
    project_id: str,
    location: str,
    scan_id: str,
    body: dict,
    token: str,
) -> tuple[bool, str]:
    """Creates or patches a Dataplex DQ scan. Returns (success, message)."""
    base_url = (
        f"https://dataplex.googleapis.com/v1/projects/{project_id}"
        f"/locations/{location}/dataScans"
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    exists = _scan_exists(project_id, location, scan_id, token)

    try:
        if exists:
            url = f"{base_url}/{scan_id}"
            # Patch all updatable fields
            params = {"updateMask": "displayName,description,executionSpec,dataQualitySpec,labels"}
            resp = http_requests.patch(url, headers=headers, params=params, json=body)
            verb = "PATCH"
        else:
            params = {"dataScanId": scan_id}
            resp = http_requests.post(base_url, headers=headers, params=params, json=body)
            verb = "POST"

        if resp.status_code in (200, 201):
            logging.info(f"[{verb}] Scan '{scan_id}' succeeded (HTTP {resp.status_code})")
            return True, "SUCCESS"
        else:
            msg = f"HTTP {resp.status_code}: {resp.text[:300]}"
            logging.error(f"[{verb}] Scan '{scan_id}' failed — {msg}")
            return False, msg

    except Exception as exc:
        msg = re.sub(r"\s+", " ", str(exc)).strip()
        logging.error(f"Exception while sending scan request for '{scan_id}': {msg}")
        return False, f"Exception: {msg}"


# ──────────────────────────────────────────────────────────────────────────────
# BQ AUDIT LOG
# ──────────────────────────────────────────────────────────────────────────────
def log_to_bigquery(bq_client: bigquery.Client, table_ref: str, entries: list[dict]):
    """Streams scan execution results to a BigQuery audit table."""
    if not entries:
        return
    errors = bq_client.insert_rows_json(table_ref, entries)
    if errors:
        logging.error(f"BigQuery streaming insert errors: {errors}")
    else:
        logging.info(f"Logged {len(entries)} entries to {table_ref}")


# ──────────────────────────────────────────────────────────────────────────────
# MAIN ORCHESTRATOR
# ──────────────────────────────────────────────────────────────────────────────
def main():
    config = load_config()
    credentials = get_credentials(config)

    gcs_conf = config["gcs"]
    bucket_name = gcs_conf["bucket_name"]
    csv_blob    = gcs_conf["csv_blob"]          # e.g. "custom_dq.csv"
    audit_table = config["dataplex"].get("audit_bq_table", "")

    bq_client = bigquery.Client(credentials=credentials) if audit_table else None

    # 1. Read the CSV from GCS
    try:
        df = read_csv_from_gcs(bucket_name, csv_blob)
    except Exception as exc:
        logging.error(f"Failed to read CSV from GCS: {exc}")
        sys.exit(1)

    required_cols = ["project_id", "location", "dataset", "table_name", "rule_keys"]
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        logging.error(f"CSV missing required columns: {missing}")
        sys.exit(1)

    # 2. Process each row
    global_failed = False
    audit_entries: list[dict] = []

    for idx, row in df.iterrows():
        row_num = idx + 2
        project_id = str(row.get("project_id", "")).strip()
        location   = str(row.get("location", config["dataplex"]["default_location"])).strip()
        dataset    = str(row.get("dataset", "")).strip()
        table_name = str(row.get("table_name", "")).strip()
        raw_keys   = str(row.get("rule_keys", "")).strip()

        if not all([project_id, location, dataset, table_name, raw_keys]):
            logging.warning(f"Row {row_num}: Missing required fields, skipping.")
            df.at[idx, "status"] = "SKIPPED: Missing fields"
            continue

        # Skip already-done rows
        current_status = str(row.get("status", "")).strip().upper()
        if current_status in ("DONE", "SUCCESS"):
            logging.info(f"Row {row_num}: Already processed. Skipping.")
            continue

        # Resolve rule keys → rule payloads
        rule_keys = [k.strip() for k in raw_keys.split("|") if k.strip()]
        resolved_rules = []
        unknown_keys   = []
        for key in rule_keys:
            if key in RULE_LIBRARY:
                try:
                    resolved_rules.append(build_rule_payload(RULE_LIBRARY[key]))
                except Exception as exc:
                    logging.error(f"Row {row_num}: Error building rule '{key}': {exc}")
                    unknown_keys.append(key)
            else:
                logging.warning(f"Row {row_num}: Rule key '{key}' not found in RULE_LIBRARY, skipping.")
                unknown_keys.append(key)

        if not resolved_rules:
            df.at[idx, "status"] = f"FAILED: No valid rules resolved from keys: {raw_keys}"
            global_failed = True
            continue

        # Build scan body and call API
        token = _get_token(credentials)
        try:
            scan_id, body = _build_scan_body(row.to_dict(), resolved_rules, config)
        except Exception as exc:
            df.at[idx, "status"] = f"FAILED: Body build error — {exc}"
            global_failed = True
            continue

        ok, msg = create_or_update_scan(project_id, location, scan_id, body, token)

        status_val = "DONE" if ok else f"FAILED: {msg}"
        df.at[idx, "status"] = status_val
        if not ok:
            global_failed = True

        if unknown_keys and ok:
            df.at[idx, "status"] = f"DONE (skipped unknown keys: {','.join(unknown_keys)})"

        # Prepare audit entry
        audit_entries.append({
            "execution_timestamp": datetime.now(timezone.utc).isoformat(),
            "project_id": project_id,
            "location": location,
            "dataset": dataset,
            "table_name": table_name,
            "scan_id": scan_id,
            "rule_keys": raw_keys,
            "status": status_val,
        })

    # 3. Write status back to GCS CSV
    upload_csv_to_gcs(bucket_name, csv_blob, df)

    # 4. Optionally log to BigQuery audit table
    if bq_client and audit_entries:
        log_to_bigquery(bq_client, audit_table, audit_entries)

    if global_failed:
        raise RuntimeError("Custom DQ scan job completed with one or more failures.")

    logging.info("Custom DQ scan Cloud Run job completed successfully.")


if __name__ == "__main__":
    try:
        main()
    except Exception as err:
        print(json.dumps({"message": str(err), "severity": "ERROR"}))
        sys.exit(1)
