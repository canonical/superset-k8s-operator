# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

variable "app_name" {
  description = "Name of the application in the Juju model."
  type        = string
  default     = "superset-k8s"
  nullable    = false

  validation {
    condition     = length(var.app_name) > 0
    error_message = "app_name must not be empty."
  }
}

variable "base" {
  description = "The operating system on which to deploy."
  type        = string
  default     = "ubuntu@22.04"
}

variable "channel" {
  description = "The channel to use when deploying the charm."
  type        = string
  default     = "latest/edge"
  nullable    = false
}

variable "config" {
  description = <<-EOT
    Application configuration, passed to the charm unchanged. Options at
    https://charmhub.io/superset-k8s/configurations. `charm-function` decides which of the three
    applications of a deployment this one is, and `signing-keys-secret-id` names the Juju secret
    every application of that deployment shares.
  EOT
  type        = map(string)
  default     = {}
  nullable    = false
}

variable "constraints" {
  description = "Juju constraints to apply for this application."
  type        = string
  default     = null
}

variable "model_uuid" {
  description = "Reference to the `juju_model` UUID to deploy to."
  type        = string
  nullable    = false
}

variable "resources" {
  description = "Map of the charm's OCI-image resources (superset-image) to use instead of the ones bundled with the deployed revision."
  type        = map(string)
  default     = {}
  nullable    = false
}

variable "revision" {
  description = "Revision number of the charm to deploy. Null deploys the latest revision on the channel."
  type        = number
  default     = null
}

variable "units" {
  description = "Number of units to deploy."
  type        = number
  default     = 1
  nullable    = false

  validation {
    condition     = var.units >= 1
    error_message = "units must be greater than or equal to 1."
  }
}
