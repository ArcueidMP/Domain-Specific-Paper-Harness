terraform {
  required_version = ">= 1.15.0, < 2.0.0"

  # Production state lives in a pre-created, versioned GCS bucket. Backend
  # values are supplied to `terraform init -backend-config=...`; keeping them
  # out of source avoids hard-coding an owner project or bucket name.
  backend "gcs" {}

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 7.41"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = "~> 7.41"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

provider "google-beta" {
  project = var.project_id
  region  = var.region
}
