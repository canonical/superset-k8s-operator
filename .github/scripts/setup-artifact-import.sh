#!/bin/bash
set -euxo pipefail

# Canonical Kubernetes has no registry addon, so the images the integration
# plan builds are pushed to a registry started here instead.
echo "Starting local registry so plan-integration can push images..."
docker run -d -p 32000:5000 --restart=always --name registry registry:2 || true
