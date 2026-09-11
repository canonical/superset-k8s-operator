# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

# Defaults match CI (operator-workflows registers the K8s cloud as `tfk8s`, no workload-storage
# override). For local runs pass globals via environment, e.g.:
#   TF_VAR_k8s_cloud_name=microk8s TF_VAR_k8s_credential_name=microk8s \
#   TF_VAR_k8s_workload_storage=microk8s-hostpath terraform test

provider "juju" {
  skip_failed_deletion = true

  default_timeouts {
    delete = "2m"
  }
}

run "setup_tests" {
  module {
    source = "./tests/setup"
  }
}

run "full_deploy" {
  variables {
    model_uuid        = run.setup_tests.model_uuid
    external_hostname = "superset.test"
  }

  assert {
    condition = alltrue([
      output.models.superset.components["superset-ui"] == "superset-k8s-ui",
      output.models.superset.components["superset-worker"] == "superset-k8s-worker",
      output.models.superset.components["superset-beat"] == "superset-k8s-beat",
      output.models.superset.components["postgresql-k8s"] == "postgresql-k8s",
      output.models.superset.components["redis-k8s"] == "redis-k8s",
      output.models.superset.components["traefik-k8s"] == "traefik-k8s",
    ])
    error_message = "the superset model components did not contain the expected applications"
  }

  assert {
    condition     = !contains(keys(output.models.superset.components), "oauth-external-idp-integrator")
    error_message = "the SSO integrator was deployed without an oauth config"
  }

  assert {
    condition     = !contains(keys(output.models.superset.components), "smtp-integrator")
    error_message = "the SMTP relay was deployed without an smtp config"
  }
}

run "wait_for_postgresql_active" {
  module {
    source = "./tests/wait_for_active"
  }

  variables {
    model_uuid = run.setup_tests.model_uuid
    app_name   = "postgresql-k8s"
    timeout    = 900
  }

  assert {
    condition     = data.external.app_status.result.status == "active"
    error_message = "postgresql-k8s did not reach active state: ${data.external.app_status.result.message}"
  }
}

run "wait_for_ui_active" {
  module {
    source = "./tests/wait_for_active"
  }

  variables {
    model_uuid = run.setup_tests.model_uuid
    app_name   = "superset-k8s-ui"
    timeout    = 1800
  }

  assert {
    condition     = data.external.app_status.result.status == "active"
    error_message = "superset-k8s-ui did not reach active state: ${data.external.app_status.result.message}"
  }
}

# The worker and the beat scheduler wait for the UI to migrate the metadata database and are
# released by the following update-status, so they reach active after it does.
run "wait_for_worker_active" {
  module {
    source = "./tests/wait_for_active"
  }

  variables {
    model_uuid = run.setup_tests.model_uuid
    app_name   = "superset-k8s-worker"
    timeout    = 900
  }

  assert {
    condition     = data.external.app_status.result.status == "active"
    error_message = "superset-k8s-worker did not reach active state: ${data.external.app_status.result.message}"
  }
}

run "wait_for_beat_active" {
  module {
    source = "./tests/wait_for_active"
  }

  variables {
    model_uuid = run.setup_tests.model_uuid
    app_name   = "superset-k8s-beat"
    timeout    = 900
  }

  assert {
    condition     = data.external.app_status.result.status == "active"
    error_message = "superset-k8s-beat did not reach active state: ${data.external.app_status.result.message}"
  }
}

run "wait_for_traefik_active" {
  module {
    source = "./tests/wait_for_active"
  }

  variables {
    model_uuid = run.setup_tests.model_uuid
    app_name   = "traefik-k8s"
    timeout    = 600
  }

  assert {
    condition     = data.external.app_status.result.status == "active"
    error_message = "traefik-k8s did not reach active state: ${data.external.app_status.result.message}"
  }
}

# Enable SSO and the SMTP relay on the now-active stack. Both are deployed on top of the base
# deploy, which is how an operator turns them on. The relay authenticates, so its password travels
# through the module's Juju secret and the Superset applications must be able to read it.
run "enable_sso_and_smtp" {
  variables {
    model_uuid        = run.setup_tests.model_uuid
    external_hostname = "superset.test"

    oauth_external_idp_integrator_config = {
      client_id     = "stub-client-id"
      client_secret = "stub-client-secret"
    }

    smtp_integrator_config = {
      host               = "smtp.example.com"
      smtp_sender        = "reports@example.com"
      port               = 587
      auth_type          = "plain"
      transport_security = "starttls"
      user               = "superset"
      password           = "stub-smtp-password"
    }
  }

  assert {
    condition = alltrue([
      output.models.superset.components["oauth-external-idp-integrator"] == "oauth-external-idp-integrator",
      output.models.superset.components["smtp-integrator"] == "smtp-integrator",
    ])
    error_message = "the SSO integrator and the SMTP relay were not deployed when configured"
  }

  assert {
    condition = alltrue([
      startswith(module.smtp_integrator[0].application.config["password_secret"], "secret:"),
      !contains(keys(module.smtp_integrator[0].application.config), "password"),
    ])
    error_message = "the SMTP password was not handed to the relay as a Juju secret"
  }
}

run "wait_for_smtp_integrator_active" {
  module {
    source = "./tests/wait_for_active"
  }

  variables {
    model_uuid = run.setup_tests.model_uuid
    app_name   = "smtp-integrator"
    timeout    = 600
  }

  assert {
    condition     = data.external.app_status.result.status == "active"
    error_message = "smtp-integrator did not reach active state: ${data.external.app_status.result.message}"
  }
}

# The UI must stay active with the oauth relation wired. Without an HTTPS ingress URL it blocks
# with `OAuth requires an HTTPS ingress URL`, which is what the certificates relation and the
# external hostname are for.
run "wait_for_ui_active_with_sso" {
  module {
    source = "./tests/wait_for_active"
  }

  variables {
    model_uuid = run.setup_tests.model_uuid
    app_name   = "superset-k8s-ui"
    timeout    = 1200
  }

  assert {
    condition     = data.external.app_status.result.status == "active"
    error_message = "superset-k8s-ui did not stay active after enabling SSO: ${data.external.app_status.result.message}"
  }
}
