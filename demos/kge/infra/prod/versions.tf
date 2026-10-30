terraform {
  required_version = "~> 1.16"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "8.8.0"
    }
    # The service agents the environment's key is granted to are made with google-beta's service identity.
    google-beta = {
      source  = "hashicorp/google-beta"
      version = "8.8.0"
    }
  }

  # Each environment's state, in a bucket of its own project's, made at bootstrap (the kit's INSTALL.md):
  #   terraform init -backend-config=qa.gcs.tfbackend -backend-config="bucket=<qa project>-tfstate"
  backend "gcs" {}
}

provider "google" {
  project               = var.project_id
  region                = var.region
  user_project_override = true
  billing_project       = var.project_id
  default_labels = {
    app         = "kge"
    environment = var.environment
    managed-by  = "terraform"
  }
}

provider "google-beta" {
  project               = var.project_id
  region                = var.region
  user_project_override = true
  billing_project       = var.project_id
  default_labels = {
    app         = "kge"
    environment = var.environment
    managed-by  = "terraform"
  }
}
