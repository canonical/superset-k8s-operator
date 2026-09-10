# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

variable "app_name" {
  description = "Name to give the deployed application."
  type        = string
  default     = "redis-k8s"
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
  description = "Map for configuration options."
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

variable "resources" {
  description = "Map of the charm's resources (redis-image, cert-file, key-file, ca-cert-file)."
  type        = map(string)
  default     = {}
}

variable "revision" {
  description = "Revision number of the charm."
  type        = number
  default     = null
}

variable "storage_directives" {
  description = "Map of storage used by the application, keyed by the charm's storage label (`database`)."
  type        = map(string)
  default     = {}
}

variable "trust" {
  description = "Whether to grant the application access to the Kubernetes cluster, which redis-k8s needs to manage its own services."
  type        = bool
  default     = true
}

variable "units" {
  description = "Unit count/scale."
  type        = number
  default     = 1
}
