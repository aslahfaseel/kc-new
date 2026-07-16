resource "google_cloud_run_v2_job" "job" {
  name     = var.job_name
  location = var.location
  project  = var.project_id
  labels   = var.labels

  template {
    labels      = var.labels
    parallelism = 1
    task_count  = 1

    template {
      service_account = var.service_account_email
      max_retries     = var.max_retries
      timeout         = "${var.timeout_seconds}s"
       vpc_access {
        connector = var.vpc_connector
        egress    = var.vpc_egress
      }

      containers {
        image = var.container_image

        dynamic "env" {
          for_each = var.env_vars
          content {
            name  = env.key
            value = env.value
          }
        }

        resources {
          limits = {
            cpu    = "1"
            memory = "512Mi"
          }
        
        }    
      }
    }
  }
}
