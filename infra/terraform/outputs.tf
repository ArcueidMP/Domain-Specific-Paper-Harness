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
  description = "Broad LLM Agents Daily Cloud Run Job name, or null before complete Daily deployment."
  value       = var.deploy_daily_resources ? google_cloud_run_v2_job.daily["broad-llm-agents"].name : null
}

output "daily_job_names" {
  description = "Daily Cloud Run Job names keyed by topic slug."
  value       = { for topic, job in google_cloud_run_v2_job.daily : topic => job.name }
}

output "migration_job_name" {
  description = "Explicit Alembic migration Cloud Run Job name, or null before it is enabled."
  value       = var.deploy_migration_resources ? google_cloud_run_v2_job.migration[0].name : null
}

output "scheduler_job_name" {
  description = "Broad LLM Agents Cloud Scheduler job name, or null before the verified scheduler gate is enabled."
  value       = var.deploy_scheduler ? google_cloud_scheduler_job.daily["broad-llm-agents"].name : null
}

output "scheduler_job_names" {
  description = "Cloud Scheduler job names keyed by topic slug."
  value       = { for topic, scheduler in google_cloud_scheduler_job.daily : topic => scheduler.name }
}

output "scheduler_paused" {
  description = "Whether the deployed broad LLM Agents Scheduler remains paused before direct verification."
  value       = var.deploy_scheduler ? google_cloud_scheduler_job.daily["broad-llm-agents"].paused : null
}

output "scheduler_paused_by_topic" {
  description = "Paused state for each deployed topic Scheduler."
  value       = { for topic, scheduler in google_cloud_scheduler_job.daily : topic => scheduler.paused }
}

output "runtime_service_accounts" {
  description = "Dedicated user-managed identities attached to each runtime boundary."
  value = {
    web       = google_service_account.web.email
    daily     = google_service_account.daily.email
    migration = google_service_account.migration.email
    scheduler = google_service_account.scheduler.email
    grobid    = var.deploy_analysis_resources ? google_service_account.grobid[0].email : null
  }
}

output "grobid_service_uri" {
  description = "IAM-private GROBID service URI, or null when analysis resources are disabled."
  value       = var.deploy_analysis_resources ? google_cloud_run_v2_service.grobid[0].uri : null
}

output "grobid_service_name" {
  description = "IAM-private GROBID Cloud Run service name, or null when disabled."
  value       = var.deploy_analysis_resources ? google_cloud_run_v2_service.grobid[0].name : null
}

output "deployment_topology" {
  description = "Non-secret applied topology and immutable inputs used to preserve staged upgrades."
  value = {
    migration = {
      deployed                = var.deploy_migration_resources
      image                   = var.deploy_migration_resources ? var.migration_image : null
      database_secret_version = var.deploy_migration_resources ? var.migration_database_secret_version : null
      timeout_seconds         = var.deploy_migration_resources ? var.migration_timeout_seconds : null
    }
    runtime = {
      deployed                         = var.deploy_runtime_resources
      analysis_deployed                = var.deploy_analysis_resources
      daily_deployed                   = var.deploy_daily_resources
      daily_topics                     = var.deploy_daily_resources ? keys(local.daily_topics) : []
      semantic_scholar_secret_attached = var.deploy_daily_resources
      web_api_image                    = var.deploy_runtime_resources ? var.web_api_image : null
      daily_image                      = var.deploy_daily_resources ? var.daily_image : null
      grobid_image                     = var.deploy_analysis_resources ? var.grobid_image : null
      database_secret_version          = (var.deploy_runtime_resources || var.deploy_daily_resources) ? var.database_secret_version : null
      daily_timeout_seconds            = var.deploy_daily_resources ? var.daily_timeout_seconds : null
      deepseek_secret_version          = var.deploy_daily_resources ? var.deepseek_secret_version : null
      semantic_scholar_secret_version  = var.deploy_daily_resources ? var.semantic_scholar_secret_version : null
    }
    scheduler = {
      deployed = var.deploy_scheduler
      paused   = var.deploy_scheduler ? var.scheduler_paused : null
      topic_schedules = var.deploy_scheduler ? {
        for topic, config in local.daily_topics : topic => config.schedule
      } : {}
    }
    identity = {
      owner_email = var.owner_email
    }
  }
}
