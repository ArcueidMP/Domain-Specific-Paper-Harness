variable "project_id" {
  description = "Existing Google Cloud project in which to create resources."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", var.project_id))
    error_message = "project_id must be a valid Google Cloud project ID."
  }
}

variable "region" {
  description = "Google Cloud region for regional resources."
  type        = string
  default     = "asia-southeast1"
}

variable "owner_email" {
  description = "Google account allowed through IAP to the private web service."
  type        = string

  validation {
    condition     = can(regex("^[^@[:space:]]+@[^@[:space:]]+\\.[^@[:space:]]+$", var.owner_email))
    error_message = "owner_email must be an email address."
  }
}

variable "name_prefix" {
  description = "Prefix used for resource names."
  type        = string
  default     = "paper-harness"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,18}[a-z0-9]$", var.name_prefix))
    error_message = "name_prefix must contain 3 to 20 lowercase letters, numbers, or hyphens."
  }
}

variable "artifact_repository_id" {
  description = "Artifact Registry Docker repository ID."
  type        = string
  default     = "paper-harness"
}

variable "database_secret_id" {
  description = "Secret Manager ID for the production PostgreSQL DATABASE_URL."
  type        = string
  default     = "paper-harness-database-url"
}

variable "deepseek_secret_id" {
  description = "Secret Manager ID reserved for the M2 DeepSeek API key."
  type        = string
  default     = "paper-harness-deepseek-api-key"
}

variable "semantic_scholar_secret_id" {
  description = "Secret Manager ID reserved for the M3 Semantic Scholar API key."
  type        = string
  default     = "paper-harness-semantic-scholar-api-key"
}

variable "demo_sync_database_secret_id" {
  description = "Secret Manager ID for the restricted Demo snapshot sync DATABASE_URL."
  type        = string
  default     = "paper-harness-demo-sync-database-url"
}

variable "demo_read_database_secret_id" {
  description = "Secret Manager ID for the read-only Demo API DATABASE_URL."
  type        = string
  default     = "paper-harness-demo-read-database-url"
}

variable "deploy_demo_sync_automation" {
  description = "Create the isolated GitHub OIDC identity and empty Demo database secret containers."
  type        = bool
  default     = false
}

variable "github_repository" {
  description = "GitHub repository in owner/name form allowed to run the main-branch Demo sync workflow."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition = !var.deploy_demo_sync_automation || (
      var.github_repository != null &&
      can(regex("^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", var.github_repository))
    )
    error_message = "github_repository must use owner/name form when Demo sync automation is enabled."
  }
}

variable "deploy_runtime_resources" {
  description = "Create the private IAP-protected Web/API service independently of the migration Job flag."
  type        = bool
  default     = false
}

variable "deploy_migration_resources" {
  description = "Create the explicit one-off Alembic migration Cloud Run Job."
  type        = bool
  default     = false
}

variable "deploy_analysis_resources" {
  description = "Create the IAM-private GROBID service independently of Web/API and Daily deployment."
  type        = bool
  default     = false
}

variable "deploy_daily_resources" {
  description = "Create the topic Daily Jobs after GROBID, database, DeepSeek, and Semantic Scholar inputs exist."
  type        = bool
  default     = false

  validation {
    condition = !var.deploy_daily_resources || (
      var.deploy_analysis_resources &&
      var.deepseek_secret_version != null &&
      can(regex("^[1-9][0-9]*$", var.deepseek_secret_version)) &&
      var.semantic_scholar_secret_version != null &&
      can(regex("^[1-9][0-9]*$", var.semantic_scholar_secret_version))
    )
    error_message = "deploy_daily_resources requires GROBID and fixed positive numeric DeepSeek and Semantic Scholar secret versions."
  }
}

variable "deploy_scheduler" {
  description = "Create one Scheduler job per topic only after migration and successful manual Daily executions."
  type        = bool
  default     = false

  validation {
    condition     = !var.deploy_scheduler || var.deploy_daily_resources
    error_message = "deploy_scheduler requires deploy_daily_resources=true."
  }
}

variable "scheduler_paused" {
  description = "Keep the created topic Scheduler jobs paused until their forced executions are verified."
  type        = bool
  default     = true

  validation {
    condition     = var.deploy_scheduler || var.scheduler_paused
    error_message = "scheduler_paused may be false only when deploy_scheduler=true."
  }
}

variable "web_api_image" {
  description = "Immutable Artifact Registry image reference for the web/API service."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition = !var.deploy_runtime_resources || (
      var.web_api_image != null && can(regex("@sha256:[0-9a-f]{64}$", var.web_api_image))
    )
    error_message = "web_api_image must be an immutable sha256 digest when runtime deployment is enabled."
  }
}

variable "migration_image" {
  description = "Immutable web/API image digest executed only by the explicit migration Job."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition     = !var.deploy_migration_resources || (var.migration_image != null && can(regex("@sha256:[0-9a-f]{64}$", var.migration_image)))
    error_message = "migration_image must be an immutable sha256 digest when migration deployment is enabled."
  }
}

variable "daily_image" {
  description = "Immutable Artifact Registry image reference shared by the topic Daily Jobs."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition     = !var.deploy_daily_resources || (var.daily_image != null && can(regex("@sha256:[0-9a-f]{64}$", var.daily_image)))
    error_message = "daily_image must be an immutable sha256 digest when Daily deployment is enabled."
  }
}

variable "grobid_image" {
  description = "Immutable Artifact Registry image reference for the verified GROBID 0.9.0-crf service."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition     = !var.deploy_analysis_resources || (var.grobid_image != null && can(regex("@sha256:[0-9a-f]{64}$", var.grobid_image)))
    error_message = "grobid_image must be an immutable sha256 digest when analysis deployment is enabled."
  }
}

variable "database_secret_version" {
  description = "Existing enabled DATABASE_URL secret version used by Web/API and Daily runtime."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition = !(var.deploy_runtime_resources || var.deploy_daily_resources) || (
      var.database_secret_version != null && can(regex("^[1-9][0-9]*$", var.database_secret_version))
    )
    error_message = "database_secret_version must be a fixed positive numeric version when Web/API or Daily deployment is enabled."
  }
}

variable "migration_database_secret_version" {
  description = "Existing enabled DATABASE_URL secret version used only by the migration Job."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition = !var.deploy_migration_resources || (
      var.migration_database_secret_version != null && can(regex("^[1-9][0-9]*$", var.migration_database_secret_version))
    )
    error_message = "migration_database_secret_version must be a fixed positive numeric version when migration deployment is enabled."
  }
}

variable "deepseek_secret_version" {
  description = "Existing enabled DeepSeek API key secret version used only by the topic Daily Jobs."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition     = var.deepseek_secret_version == null || can(regex("^[1-9][0-9]*$", var.deepseek_secret_version))
    error_message = "deepseek_secret_version must be null or a fixed positive numeric version."
  }
}

variable "semantic_scholar_secret_version" {
  description = "Existing enabled Semantic Scholar API key secret version used only by topic Daily Job operations."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition     = var.semantic_scholar_secret_version == null || can(regex("^[1-9][0-9]*$", var.semantic_scholar_secret_version))
    error_message = "semantic_scholar_secret_version must be null or a fixed positive numeric version."
  }
}

variable "web_max_instances" {
  description = "Maximum web/API Cloud Run instances. Minimum is fixed at zero."
  type        = number
  default     = 2

  validation {
    condition     = var.web_max_instances >= 1 && var.web_max_instances <= 10
    error_message = "web_max_instances must be between 1 and 10."
  }
}

variable "daily_timeout_seconds" {
  description = "Cloud Run task envelope in seconds; it must exceed the explicit 28800-second application pipeline budget."
  type        = number
  default     = 30000

  validation {
    condition     = var.daily_timeout_seconds > 28800 && var.daily_timeout_seconds <= 86400 && floor(var.daily_timeout_seconds) == var.daily_timeout_seconds
    error_message = "daily_timeout_seconds must be a whole number greater than 28800 and at most 86400."
  }
}

variable "migration_timeout_seconds" {
  description = "Maximum duration of the explicit Alembic migration task in seconds."
  type        = number
  default     = 900

  validation {
    condition     = var.migration_timeout_seconds >= 300 && var.migration_timeout_seconds <= 3600 && floor(var.migration_timeout_seconds) == var.migration_timeout_seconds
    error_message = "migration_timeout_seconds must be a whole number between 300 and 3600."
  }
}

variable "schedule" {
  description = "Broad LLM Agents Daily Job cron expression."
  type        = string
  default     = "0 5 * * *"
}

variable "brain_computer_interfaces_schedule" {
  description = "Brain-Computer Interfaces Daily Job cron expression."
  type        = string
  default     = "20 5 * * *"
}

variable "world_models_schedule" {
  description = "World Models Daily Job cron expression."
  type        = string
  default     = "40 5 * * *"
}

variable "schedule_time_zone" {
  description = "IANA time zone used to interpret every topic schedule."
  type        = string
  default     = "Asia/Kuala_Lumpur"
}
