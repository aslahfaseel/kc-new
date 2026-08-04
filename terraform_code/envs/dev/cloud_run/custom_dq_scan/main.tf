module "custom_dq_scan_job" {
  source = "../../../../custom_modules/cloudrun_job"

  project_id  = var.project_id
  location    = var.location
  job_name    = "custom-dq-scan"

  # CI/CD passes the commit hash via var.image_tag at deploy time
  container_image = "us-east4-docker.pkg.dev/${var.project_id}/gkc-governance-repo/custom_dq_scan:${var.image_tag}"

  service_account_email = var.terraform_sa
  
  vpc_connector = "projects/vz-it-np-exhv-sharedvpc-228116/locations/us-east4/connectors/shared-np-east"
  vpc_egress    = "ALL_TRAFFIC"
  kms_key       = "projects/vz-it-np-d0sv-vsadkms-0/locations/us-east4/keyRings/vz-nonit-np-kr-vgs/cryptoKeys/vz-nonit-np-kms-jyav"

  max_retries     = 3
  timeout_seconds = 3600

  env_vars = {
    GCS_BUCKET_NAME     = var.gcs_bucket_name
    GCS_CSV_BLOB        = "custom_dq.csv"
    DATAPLEX_PROJECT_ID = var.project_id
    DATAPLEX_LOCATION   = var.location
    DQ_RESULTS_TABLE    = "//bigquery.googleapis.com/projects/${var.project_id}/datasets/gkc_governance_tbls/tables/data_quality_results"
    AUDIT_BQ_TABLE      = "${var.project_id}.gkc_governance_tbls.custom_dq_scan_audit"
  }

  labels = {
    purpose = "custom-dq-scan"
  }
}
