locals {
  demo_github_repository = coalesce(var.github_repository, "disabled/disabled")
}

resource "google_service_account" "demo_sync" {
  count = var.deploy_demo_sync_automation ? 1 : 0

  project      = var.project_id
  account_id   = "${var.name_prefix}-demo-sync"
  display_name = "Paper Harness Demo snapshot sync"

  depends_on = [google_project_service.required["iam.googleapis.com"]]
}

resource "google_iam_workload_identity_pool" "demo_sync" {
  count = var.deploy_demo_sync_automation ? 1 : 0

  project                   = var.project_id
  workload_identity_pool_id = "${var.name_prefix}-github"
  display_name              = "Paper Harness GitHub"
  description               = "GitHub OIDC identities for non-blocking Demo data synchronization"

  depends_on = [google_project_service.required["iam.googleapis.com"]]
}

resource "google_iam_workload_identity_pool_provider" "demo_sync" {
  count = var.deploy_demo_sync_automation ? 1 : 0

  project                            = var.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.demo_sync[0].workload_identity_pool_id
  workload_identity_pool_provider_id = "${var.name_prefix}-main"
  display_name                       = "Paper Harness Demo sync"

  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
  }
  attribute_condition = <<-EOT
    assertion.repository == "${local.demo_github_repository}" &&
    assertion.ref == "refs/heads/main" &&
    assertion.workflow_ref == "${local.demo_github_repository}/.github/workflows/demo-data-sync.yml@refs/heads/main"
  EOT

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }

  depends_on = [google_project_service.required["iamcredentials.googleapis.com"]]
}

resource "google_service_account_iam_member" "demo_sync_workload_identity" {
  count = var.deploy_demo_sync_automation ? 1 : 0

  service_account_id = google_service_account.demo_sync[0].name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.demo_sync[0].name}/attribute.repository/${local.demo_github_repository}"
}

resource "google_secret_manager_secret_iam_member" "demo_sync_database_accessor" {
  count = var.deploy_demo_sync_automation ? 1 : 0

  project   = var.project_id
  secret_id = google_secret_manager_secret.demo_sync_database_url[0].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.demo_sync[0].email}"
}
