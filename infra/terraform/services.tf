locals {
  required_services = toset([
    "artifactregistry.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "cloudscheduler.googleapis.com",
    "iam.googleapis.com",
    "iap.googleapis.com",
    "logging.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
  ])
}

data "google_project" "current" {
  project_id = var.project_id
}

resource "google_project_service" "required" {
  for_each = local.required_services

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

resource "google_artifact_registry_repository" "containers" {
  project       = var.project_id
  location      = var.region
  repository_id = var.artifact_repository_id
  description   = "Domain-Specific Paper Harness runtime images"
  format        = "DOCKER"

  docker_config {
    immutable_tags = true
  }

  depends_on = [google_project_service.required["artifactregistry.googleapis.com"]]
}

resource "google_secret_manager_secret" "database_url" {
  project   = var.project_id
  secret_id = var.database_secret_id

  replication {
    auto {}
  }

  depends_on = [google_project_service.required["secretmanager.googleapis.com"]]
}

resource "google_secret_manager_secret" "deepseek_api_key" {
  project   = var.project_id
  secret_id = var.deepseek_secret_id

  replication {
    auto {}
  }

  depends_on = [google_project_service.required["secretmanager.googleapis.com"]]
}

resource "google_secret_manager_secret" "semantic_scholar_api_key" {
  project   = var.project_id
  secret_id = var.semantic_scholar_secret_id

  replication {
    auto {}
  }

  depends_on = [google_project_service.required["secretmanager.googleapis.com"]]
}
