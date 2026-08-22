resource "google_service_account" "web" {
  project      = var.project_id
  account_id   = "${var.name_prefix}-web"
  display_name = "Paper Harness web and API runtime"

  depends_on = [google_project_service.required["iam.googleapis.com"]]
}

resource "google_service_account" "daily" {
  project      = var.project_id
  account_id   = "${var.name_prefix}-daily"
  display_name = "Paper Harness Daily Job runtime"

  depends_on = [google_project_service.required["iam.googleapis.com"]]
}

resource "google_service_account" "scheduler" {
  project      = var.project_id
  account_id   = "${var.name_prefix}-scheduler"
  display_name = "Paper Harness Scheduler invoker"

  depends_on = [google_project_service.required["iam.googleapis.com"]]
}

resource "google_service_account" "migration" {
  project      = var.project_id
  account_id   = "${var.name_prefix}-migration"
  display_name = "Paper Harness one-off database migration"

  depends_on = [google_project_service.required["iam.googleapis.com"]]
}

resource "google_service_account" "grobid" {
  count = var.deploy_analysis_resources ? 1 : 0

  project      = var.project_id
  account_id   = "${var.name_prefix}-grobid"
  display_name = "Paper Harness private GROBID runtime"

  depends_on = [google_project_service.required["iam.googleapis.com"]]
}

resource "google_secret_manager_secret_iam_binding" "database_accessors" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.database_url.secret_id
  role      = "roles/secretmanager.secretAccessor"
  members = [
    "serviceAccount:${google_service_account.daily.email}",
    "serviceAccount:${google_service_account.migration.email}",
    "serviceAccount:${google_service_account.web.email}",
  ]
}

resource "google_secret_manager_secret_iam_binding" "deepseek_accessors" {
  count = var.deploy_daily_resources ? 1 : 0

  project   = var.project_id
  secret_id = google_secret_manager_secret.deepseek_api_key.secret_id
  role      = "roles/secretmanager.secretAccessor"
  members   = ["serviceAccount:${google_service_account.daily.email}"]
}

resource "google_secret_manager_secret_iam_binding" "semantic_scholar_accessors" {
  count = var.deploy_daily_resources ? 1 : 0

  project   = var.project_id
  secret_id = google_secret_manager_secret.semantic_scholar_api_key.secret_id
  role      = "roles/secretmanager.secretAccessor"
  members   = ["serviceAccount:${google_service_account.daily.email}"]
}
