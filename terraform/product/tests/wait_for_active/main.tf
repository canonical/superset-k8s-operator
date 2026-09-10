# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

terraform {
  required_version = "~> 1.14"
  required_providers {
    external = {
      version = "> 2"
      source  = "hashicorp/external"
    }
    juju = {
      version = "~> 2.0"
      source  = "juju/juju"
    }
  }
}

provider "juju" {}

variable "model_uuid" {
  type = string
}

variable "app_name" {
  type = string
}

variable "timeout" {
  type = number
}

# tflint-ignore: terraform_unused_declarations
data "external" "app_status" {
  program = ["bash", "${path.module}/wait-for-active.sh", var.model_uuid, var.app_name, var.timeout]
}
