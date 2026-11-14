terraform {
  required_version = "~> 1.16"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "8.10.0"
    }
    # Vertex AI's service agent, which reads a large document from the tmp bucket, is made with google-beta's service
    # identity, as prod/ makes the agents its key is granted to.
    google-beta = {
      source  = "hashicorp/google-beta"
      version = "8.10.0"
    }
  }

  # The state, in a bucket of the project's own made at bootstrap (the kit's INSTALL.md):
  #   terraform init -backend-config="bucket=<project>-tfstate" -backend-config="prefix=kge/pilot"
  backend "gcs" {}
}

# Every call is billed to the project and checked against its quota, as the budget's API requires of a person's
# credentials (Cloud Shell's).
provider "google" {
  project               = var.project_id
  region                = var.region
  user_project_override = true
  billing_project       = var.project_id
  default_labels = {
    app         = "kge"
    environment = "pilot"
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
    environment = "pilot"
    managed-by  = "terraform"
  }
}
