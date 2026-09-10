# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

variable "app_name" {
  description = "Name to give the deployed application."
  type        = string
  default     = "oauth-external-idp-integrator"
  nullable    = false
}

variable "base" {
  description = "The operating system on which to deploy. E.g. ubuntu@22.04."
  type        = string
  default     = "ubuntu@22.04"
}

variable "channel" {
  description = "Channel of the charm."
  type        = string
  default     = "latest/edge"
  nullable    = false
}

variable "config" {
  description = "Map for configuration options (issuer/endpoints, client credentials, scope)."
  type        = map(string)
  default     = {}
}

variable "constraints" {
  description = "String listing constraints for this application."
  type        = string
  default     = "arch=amd64"
}

variable "model_uuid" {
  description = "Reference to an existing model uuid."
  type        = string
  nullable    = false
}

variable "revision" {
  description = "Revision number of the charm."
  type        = number
  default     = null
}

variable "units" {
  description = "Unit count/scale."
  type        = number
  default     = 1
}
