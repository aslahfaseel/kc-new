module "cloudrun_job" {
  source = "../../../../custom_modules/cloudrun_job"

  project_id            = var.project_id
  location              = var.location
  job_name              = "data-insights"
  
  # Notice we are using var.image_tag now, which Jenkins will pass automatically
  container_image       = "us-east4-docker.pkg.dev/${var.project_id}/vz-it-keiv-dpev-0-docker/data_insights:${var.image_tag}"
  
  service_account_email = var.terraform_sa
  vpc_connector         = "projects/vz-it-np-exhv-sharedvpc-228116/locations/us-east4/connectors/shared-np-east"
  vpc_egress            = "ALL_TRAFFIC"
  
  max_retries           = 3
  timeout_seconds       = 3600

  env_vars = {

  }

  labels = {
    purpose = "data-insights"
  }
}
