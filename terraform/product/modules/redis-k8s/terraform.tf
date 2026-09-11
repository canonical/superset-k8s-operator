# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

terraform {
  required_version = "~> 1.14"
  required_providers {
    juju = {
      source  = "juju/juju"
      version = "~> 2.0"
    }
  }
}
