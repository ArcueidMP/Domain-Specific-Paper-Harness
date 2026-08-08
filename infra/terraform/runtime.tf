resource "google_cloud_run_v2_service" "web" {
  count = var.deploy_runtime_resources ? 1 : 0

  project             = var.project_id
  name                = "${var.name_prefix}-web"
  location            = var.region
  deletion_protection = true
  ingress             = "INGRESS_TRAFFIC_ALL"
  iap_enabled         = true

  template {
    service_account = google_service_account.web.email
    timeout         = "300s"

    scaling {
      min_instance_count = 0
      max_instance_count = var.web_max_instances
    }

    containers {
      image = var.web_api_image

      ports {
        name           = "http1"
        container_port = 8080
      }

      env {
        name  = "APP_ENV"
        value = "production"
      }

      env {
        name = "DATABASE_URL"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.database_url.secret_id
            version = var.database_secret_version
          }
        }
      }

      startup_probe {
        initial_delay_seconds = 1
        timeout_seconds       = 3
        period_seconds        = 5
        failure_threshold     = 12

        http_get {
          path = "/health/live"
          port = 8080
        }
      }

      liveness_probe {
        timeout_seconds   = 3
        period_seconds    = 30
        failure_threshold = 3

        http_get {
          path = "/health/live"
          port = 8080
        }
      }
    }
  }

  traffic {
    type    = "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST"
    percent = 100
  }

  depends_on = [
    google_project_service.required["run.googleapis.com"],
    google_project_service.required["iap.googleapis.com"],
    google_secret_manager_secret_iam_member.web_database,
  ]
}

resource "google_cloud_run_v2_service_iam_binding" "iap_invoker" {
  count = var.deploy_runtime_resources ? 1 : 0

  project  = var.project_id
  location = google_cloud_run_v2_service.web[0].location
  name     = google_cloud_run_v2_service.web[0].name
  role     = "roles/run.invoker"
  members = [
    "serviceAccount:service-${data.google_project.current.number}@gcp-sa-iap.iam.gserviceaccount.com",
  ]

  depends_on = [google_project_service.required["iap.googleapis.com"]]
}

resource "google_iap_web_cloud_run_service_iam_binding" "owner" {
  count = var.deploy_runtime_resources ? 1 : 0

  project                = var.project_id
  location               = google_cloud_run_v2_service.web[0].location
  cloud_run_service_name = google_cloud_run_v2_service.web[0].name
  role                   = "roles/iap.httpsResourceAccessor"
  members                = ["user:${var.owner_email}"]

  depends_on = [google_cloud_run_v2_service_iam_binding.iap_invoker]
}

resource "google_cloud_run_v2_job" "daily" {
  count = var.deploy_runtime_resources ? 1 : 0

  project             = var.project_id
  name                = "${var.name_prefix}-daily"
  location            = var.region
  deletion_protection = true

  template {
    task_count  = 1
    parallelism = 1

    template {
      service_account = google_service_account.daily.email
      timeout         = var.daily_timeout
      max_retries     = 0

      containers {
        image = var.daily_image

        env {
          name  = "APP_ENV"
          value = "production"
        }

        env {
          name  = "TOPIC_CONFIG_PATH"
          value = "/app/configs/topics/broad-llm-agents.yaml"
        }

        env {
          name = "DATABASE_URL"
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.database_url.secret_id
              version = var.database_secret_version
            }
          }
        }
      }
    }
  }

  depends_on = [
    google_project_service.required["run.googleapis.com"],
    google_secret_manager_secret_iam_member.daily_database,
  ]
}

resource "google_cloud_run_v2_job_iam_member" "scheduler_invoker" {
  count = var.deploy_runtime_resources ? 1 : 0

  project  = var.project_id
  location = google_cloud_run_v2_job.daily[0].location
  name     = google_cloud_run_v2_job.daily[0].name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.scheduler.email}"
}

resource "google_cloud_scheduler_job" "daily" {
  count = var.deploy_runtime_resources ? 1 : 0

  project          = var.project_id
  region           = var.region
  name             = "${var.name_prefix}-daily"
  description      = "Execute the Paper Harness Daily Job"
  schedule         = var.schedule
  time_zone        = var.schedule_time_zone
  attempt_deadline = "320s"

  retry_config {
    retry_count          = 2
    max_retry_duration   = "600s"
    min_backoff_duration = "30s"
    max_backoff_duration = "300s"
    max_doublings        = 3
  }

  http_target {
    http_method = "POST"
    uri         = "https://run.googleapis.com/v2/projects/${var.project_id}/locations/${var.region}/jobs/${google_cloud_run_v2_job.daily[0].name}:run"
    body        = base64encode("{}")

    headers = {
      "Content-Type" = "application/json"
    }

    oauth_token {
      service_account_email = google_service_account.scheduler.email
      scope                 = "https://www.googleapis.com/auth/cloud-platform"
    }
  }

  depends_on = [
    google_project_service.required["cloudscheduler.googleapis.com"],
    google_cloud_run_v2_job_iam_member.scheduler_invoker,
  ]
}
