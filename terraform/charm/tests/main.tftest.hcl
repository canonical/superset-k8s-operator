# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

# Defaults match CI (operator-workflows registers the K8s cloud as `tfk8s`, no workload-storage
# override). For local runs pass globals via environment, e.g.:
#   TF_VAR_k8s_cloud_name=microk8s TF_VAR_k8s_credential_name=microk8s \
#   TF_VAR_k8s_workload_storage=microk8s-hostpath terraform test

run "setup_tests" {
  module {
    source = "./tests/setup"
  }
}

# The charm blocks without its signing keys and its two mandatory relations, which an apply does
# not wait for: the provider returns once the units are allocated.
run "basic_deploy" {
  variables {
    model_uuid = run.setup_tests.model_uuid
    channel    = "latest/edge"
  }

  assert {
    condition     = output.application.name == "superset-k8s"
    error_message = "default app_name did not match expected superset-k8s"
  }

  assert {
    condition = alltrue([
      output.requires["postgresql_db"].name == "superset-k8s",
      output.requires["postgresql_db"].endpoint == "postgresql_db",
      output.requires["redis"].endpoint == "redis",
      output.requires["ingress"].endpoint == "ingress",
      output.requires["oauth"].endpoint == "oauth",
      output.requires["smtp"].endpoint == "smtp",
      output.requires["trino_catalog"].endpoint == "trino-catalog",
      output.requires["certificates"].endpoint == "certificates",
      output.requires["logging"].endpoint == "logging",
    ])
    error_message = "requires endpoint objects did not match the expected shape"
  }

  assert {
    condition = alltrue([
      output.provides["metrics_endpoint"].name == "superset-k8s",
      output.provides["metrics_endpoint"].endpoint == "metrics-endpoint",
      output.provides["grafana_dashboard"].endpoint == "grafana-dashboard",
    ])
    error_message = "provides endpoint objects did not match the expected shape"
  }
}

# One charm serves the three functions of a deployment, so the module has to be instantiable more
# than once against the same model with a different name and function.
run "worker_deploy" {
  command = plan

  variables {
    model_uuid = run.setup_tests.model_uuid
    app_name   = "superset-k8s-worker"
    config     = { "charm-function" = "worker" }
    resources  = { "superset-image" = "docker.io/example/superset-image:test" }
  }

  assert {
    condition     = output.application.config["charm-function"] == "worker"
    error_message = "config was not forwarded to the application unchanged"
  }

  assert {
    condition     = output.application.resources["superset-image"] == "docker.io/example/superset-image:test"
    error_message = "resource override was not forwarded to the application"
  }

  assert {
    condition     = output.requires["postgresql_db"].name == "superset-k8s-worker"
    error_message = "endpoint objects did not follow app_name"
  }
}

run "invalid_units" {
  command = plan

  variables {
    model_uuid = run.setup_tests.model_uuid
    units      = 0
  }

  expect_failures = [var.units]
}
