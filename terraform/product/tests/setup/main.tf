# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

terraform {
  required_version = "~> 1.14"
  required_providers {
    juju = {
      version = "~> 2.0"
      source  = "juju/juju"
    }
  }
}

provider "juju" {
  skip_failed_deletion = true

  default_timeouts {
    delete = "2m"
  }
}

variable "k8s_cloud_name" {
  description = "Name of the Kubernetes cloud registered on the controller."
  type        = string
  default     = "tfk8s"
}

variable "k8s_credential_name" {
  description = "Name of the credential for the Kubernetes cloud."
  type        = string
  default     = "tfk8s"
}

variable "k8s_workload_storage" {
  description = "StorageClass for workloads in the K8s model. Empty uses the cloud default."
  type        = string
  default     = ""
}

resource "juju_model" "superset" {
  name       = "tf-testing-superset-${formatdate("YYYYMMDDhhmmss", timestamp())}"
  credential = var.k8s_credential_name

  cloud {
    name = var.k8s_cloud_name
  }

  config = var.k8s_workload_storage != "" ? { workload-storage = var.k8s_workload_storage } : {}
}

output "model_uuid" {
  value = juju_model.superset.uuid
}
