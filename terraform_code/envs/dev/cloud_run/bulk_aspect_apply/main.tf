module "cloudrun_job" {
  source = "../../../../custom_modules/cloudrun_job"

  project_id            = var.project_id
  location              = var.location
  job_name              = "bulk-aspect-apply"
  
  # Notice we are using var.image_tag now, which Jenkins will pass automatically
  container_image       = "us-east4-docker.pkg.dev/${var.project_id}/vz-it-keiv-dpev-0-docker/bulk_aspect_apply:${var.image_tag}"
  
  service_account_email = var.terraform_sa
  vpc_connector         = "projects/vz-it-np-exhv-sharedvpc-228116/locations/us-east4/connectors/shared-np-east"
  vpc_egress            = "ALL_TRAFFIC"
  
  max_retries           = 3
  timeout_seconds       = 3600

  env_vars = {
    GCS_BUCKET_NAME     = var.aspect_patcher_gcs_bucket
    GCS_CSV_PATH        = "data/vz_aspect_assignment.csv"
    DATAPLEX_PROJECT_ID = var.project_id
    DATAPLEX_LOCATION   = var.location
    GOVERNANCE_PROJECT  = var.project_id
    CURATED_PROJECT     = var.project_id
    DLP_RESULTS_TABLE   = "${var.project_id}.vzdataset.sdp_results"
    MAPPING_TABLE       = "${var.project_id}.vzdataset.infotype_mapping_local"
    RECOMMENDED_TABLE   = "${var.project_id}.vzdataset.recommended_classification"
  }

  labels = {
    purpose = "bulk-aspect-apply"
  }
}
