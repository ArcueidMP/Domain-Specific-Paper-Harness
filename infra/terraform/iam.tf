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

resource "google_project_iam_member" "web_log_writer" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.web.email}"
}

resource "google_project_iam_member" "daily_log_writer" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.daily.email}"
}

resource "google_secret_manager_secret_iam_member" "web_database" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.database_url.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.web.email}"
}

resource "google_secret_manager_secret_iam_member" "daily_database" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.database_url.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.daily.email}"
}
