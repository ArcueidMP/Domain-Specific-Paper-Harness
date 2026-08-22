resource "google_cloud_run_v2_job" "migration" {
  count = var.deploy_migration_resources ? 1 : 0

  project             = var.project_id
  name                = "${var.name_prefix}-migration"
  location            = var.region
  deletion_protection = true

  template {
    task_count  = 1
    parallelism = 1

    template {
      service_account = google_service_account.migration.email
      timeout         = "${var.migration_timeout_seconds}s"
      max_retries     = 0

      containers {
        image   = var.migration_image
        command = ["alembic"]
        args    = ["upgrade", "head"]

        resources {
          limits = {
            cpu    = "1"
            memory = "1Gi"
          }
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
              version = var.migration_database_secret_version
            }
          }
        }
      }
    }
  }

  depends_on = [
    google_project_service.required["run.googleapis.com"],
    google_secret_manager_secret_iam_binding.database_accessors,
  ]
}

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
    # Each instance owns a three-connection SQLAlchemy pool with no overflow.
    # Two user requests leave one connection available to the database-backed
    # readiness probe, whose timeout is intentionally shorter than pool wait.
    max_instance_request_concurrency = 2

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

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
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

      readiness_probe {
        timeout_seconds   = 3
        period_seconds    = 5
        failure_threshold = 3

        http_get {
          path = "/health/ready"
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
    google_secret_manager_secret_iam_binding.database_accessors,
  ]
}

resource "google_cloud_run_v2_service_iam_binding" "iap_invoker" {
  count = var.deploy_runtime_resources ? 1 : 0

  project  = var.project_id
  location = google_cloud_run_v2_service.web[0].location
  name     = google_cloud_run_v2_service.web[0].name
  role     = "roles/run.invoker"
  members = [
    "serviceAccount:${google_project_service_identity.iap.email}",
  ]

  depends_on = [google_project_service_identity.iap]
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

resource "google_cloud_run_v2_service" "grobid" {
  count = var.deploy_analysis_resources ? 1 : 0

  project              = var.project_id
  name                 = "${var.name_prefix}-grobid"
  location             = var.region
  description          = "IAM-private GROBID 0.9.0 CRF scientific PDF parser"
  deletion_protection  = true
  invoker_iam_disabled = false

  # Cloud Run-to-Cloud Run IAM authentication works without fixed-cost VPC,
  # load-balancer, or NAT resources. There is deliberately no public invoker.
  ingress = "INGRESS_TRAFFIC_ALL"

  labels = {
    "paper-harness-runtime" = "grobid"
  }

  template {
    service_account                  = google_service_account.grobid[0].email
    timeout                          = "900s"
    execution_environment            = "EXECUTION_ENVIRONMENT_GEN2"
    max_instance_request_concurrency = 1

    scaling {
      min_instance_count = 0
      max_instance_count = 1
    }

    containers {
      name  = "grobid"
      image = var.grobid_image

      ports {
        name           = "http1"
        container_port = 8070
      }

      resources {
        limits = {
          cpu    = "4"
          memory = "8Gi"
        }
        cpu_idle          = true
        startup_cpu_boost = true
      }

      startup_probe {
        initial_delay_seconds = 0
        timeout_seconds       = 5
        period_seconds        = 10
        failure_threshold     = 24

        http_get {
          path = "/api/health"
          port = 8070
        }
      }

      readiness_probe {
        timeout_seconds   = 5
        period_seconds    = 10
        failure_threshold = 3

        http_get {
          path = "/api/health"
          port = 8070
        }
      }

      liveness_probe {
        initial_delay_seconds = 10
        timeout_seconds       = 5
        period_seconds        = 30
        failure_threshold     = 3

        http_get {
          path = "/api/isalive"
          port = 8070
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
  ]
}

resource "google_cloud_run_v2_service_iam_binding" "grobid_invokers" {
  count = var.deploy_analysis_resources ? 1 : 0

  project  = var.project_id
  location = google_cloud_run_v2_service.grobid[0].location
  name     = google_cloud_run_v2_service.grobid[0].name
  role     = "roles/run.invoker"
  members  = ["serviceAccount:${google_service_account.daily.email}"]
}

resource "google_cloud_run_v2_job" "daily" {
  count = var.deploy_daily_resources ? 1 : 0

  project             = var.project_id
  name                = "${var.name_prefix}-daily"
  location            = var.region
  deletion_protection = true

  template {
    task_count  = 1
    parallelism = 1

    template {
      service_account = google_service_account.daily.email
      timeout         = "${var.daily_timeout_seconds}s"
      max_retries     = 0

      containers {
        image   = var.daily_image
        command = ["paper-harness-daily"]
        args = [
          "run-pipeline",
          "--topic-config",
          "/app/configs/topics/broad-llm-agents.yaml",
          "--analysis-scope",
          "full_text",
          "--narrative-mode",
          "deepseek",
          "--max-selected-papers",
          "10",
          "--backfill-max-queries",
          "8",
          "--backfill-per-query-limit",
          "100",
          "--backfill-timeout-seconds",
          "1800",
          "--max-search-steps",
          "12",
          "--max-search-queries",
          "4",
          "--max-search-queue-size",
          "100",
          "--max-citation-depth",
          "2",
          "--max-search-candidates",
          "100",
          "--max-selected-candidates",
          "5",
          "--search-operation-timeout-seconds",
          "60",
          "--search-overall-timeout-seconds",
          "300",
          "--max-comparisons-per-paper",
          "3",
          "--pipeline-timeout-seconds",
          "28800",
        ]

        resources {
          limits = {
            cpu    = "4"
            memory = "16Gi"
          }
        }

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

        dynamic "env" {
          for_each = var.deploy_daily_resources ? tomap({
            ANALYSIS_MODE    = "full_text"
            GROBID_AUDIENCE  = google_cloud_run_v2_service.grobid[0].uri
            GROBID_AUTH_MODE = "google_identity"
            GROBID_URL       = google_cloud_run_v2_service.grobid[0].uri
            LLM_MODEL        = "deepseek-v4-flash"
            LLM_PROVIDER     = "deepseek"
          }) : tomap({})

          content {
            name  = env.key
            value = env.value
          }
        }

        dynamic "env" {
          for_each = var.deploy_daily_resources ? [var.deepseek_secret_version] : []
          iterator = deepseek_secret

          content {
            name = "DEEPSEEK_API_KEY"
            value_source {
              secret_key_ref {
                secret  = google_secret_manager_secret.deepseek_api_key.secret_id
                version = deepseek_secret.value
              }
            }
          }
        }

        dynamic "env" {
          for_each = var.deploy_daily_resources ? [var.semantic_scholar_secret_version] : []
          iterator = semantic_scholar_secret

          content {
            name = "SEMANTIC_SCHOLAR_API_KEY"
            value_source {
              secret_key_ref {
                secret  = google_secret_manager_secret.semantic_scholar_api_key.secret_id
                version = semantic_scholar_secret.value
              }
            }
          }
        }
      }
    }
  }

  depends_on = [
    google_project_service.required["run.googleapis.com"],
    google_secret_manager_secret_iam_binding.database_accessors,
    google_secret_manager_secret_iam_binding.deepseek_accessors,
    google_secret_manager_secret_iam_binding.semantic_scholar_accessors,
    google_cloud_run_v2_service_iam_binding.grobid_invokers,
  ]
}

resource "google_cloud_run_v2_job_iam_binding" "scheduler_invokers" {
  count = var.deploy_scheduler ? 1 : 0

  project  = var.project_id
  location = google_cloud_run_v2_job.daily[0].location
  name     = google_cloud_run_v2_job.daily[0].name
  role     = "roles/run.invoker"
  members  = ["serviceAccount:${google_service_account.scheduler.email}"]
}

resource "google_cloud_scheduler_job" "daily" {
  count = var.deploy_scheduler ? 1 : 0

  project          = var.project_id
  region           = var.region
  name             = "${var.name_prefix}-daily"
  description      = "Execute the Paper Harness Daily Job"
  schedule         = var.schedule
  time_zone        = var.schedule_time_zone
  attempt_deadline = "320s"
  paused           = var.scheduler_paused

  retry_config {
    retry_count          = 0
    max_retry_duration   = "0s"
    min_backoff_duration = "5s"
    max_backoff_duration = "5s"
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
    google_cloud_run_v2_job_iam_binding.scheduler_invokers,
  ]
}
