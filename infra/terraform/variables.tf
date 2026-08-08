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

variable "deploy_runtime_resources" {
  description = "Create Cloud Run and Scheduler resources after images and secret versions exist."
  type        = bool
  default     = false
}

variable "deploy_analysis_resources" {
  description = "Add the M2 DeepSeek configuration and private GROBID service to an enabled runtime deployment."
  type        = bool
  default     = false

  validation {
    condition     = !var.deploy_analysis_resources || var.deploy_runtime_resources
    error_message = "deploy_analysis_resources requires deploy_runtime_resources=true."
  }
}

variable "web_api_image" {
  description = "Immutable Artifact Registry image reference for the web/API service."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition     = !var.deploy_runtime_resources || (var.web_api_image != null && can(regex("@sha256:[0-9a-f]{64}$", var.web_api_image)))
    error_message = "web_api_image must be an immutable sha256 digest when runtime deployment is enabled."
  }
}

variable "daily_image" {
  description = "Immutable Artifact Registry image reference for the Daily Job."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition     = !var.deploy_runtime_resources || (var.daily_image != null && can(regex("@sha256:[0-9a-f]{64}$", var.daily_image)))
    error_message = "daily_image must be an immutable sha256 digest when runtime deployment is enabled."
  }
}

variable "grobid_image" {
  description = "Immutable Artifact Registry image reference for the verified GROBID 0.9.0-crf wrapper."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition     = !var.deploy_analysis_resources || (var.grobid_image != null && can(regex("@sha256:[0-9a-f]{64}$", var.grobid_image)))
    error_message = "grobid_image must be an immutable sha256 digest when analysis deployment is enabled."
  }
}

variable "database_secret_version" {
  description = "Existing enabled DATABASE_URL secret version used by Cloud Run."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition     = !var.deploy_runtime_resources || (var.database_secret_version != null && can(regex("^[1-9][0-9]*$", var.database_secret_version)))
    error_message = "database_secret_version must be a fixed positive numeric version when runtime deployment is enabled."
  }
}

variable "deepseek_secret_version" {
  description = "Existing enabled DeepSeek API key secret version used only by the M2 Daily Job."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition     = !var.deploy_analysis_resources || (var.deepseek_secret_version != null && can(regex("^[1-9][0-9]*$", var.deepseek_secret_version)))
    error_message = "deepseek_secret_version must be a fixed positive numeric version when analysis deployment is enabled."
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

variable "daily_timeout" {
  description = "Maximum duration of one Daily Job task."
  type        = string
  default     = "3600s"
}

variable "schedule" {
  description = "Daily Job cron expression."
  type        = string
  default     = "0 5 * * *"
}

variable "schedule_time_zone" {
  description = "IANA time zone used to interpret the daily schedule."
  type        = string
  default     = "Asia/Kuala_Lumpur"
}
