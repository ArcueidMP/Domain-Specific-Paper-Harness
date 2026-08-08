output "artifact_registry_repository" {
  description = "Artifact Registry repository path used for runtime images."
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.containers.repository_id}"
}

output "secret_ids" {
  description = "Secret resources that require externally supplied versions."
  value = {
    database_url     = google_secret_manager_secret.database_url.secret_id
    deepseek_api_key = google_secret_manager_secret.deepseek_api_key.secret_id
    semantic_scholar = google_secret_manager_secret.semantic_scholar_api_key.secret_id
  }
}

output "web_service_uri" {
  description = "IAP-protected web/API URI, or null before runtime deployment."
  value       = var.deploy_runtime_resources ? google_cloud_run_v2_service.web[0].uri : null
}

output "daily_job_name" {
  description = "Daily Cloud Run Job name, or null before runtime deployment."
  value       = var.deploy_runtime_resources ? google_cloud_run_v2_job.daily[0].name : null
}
