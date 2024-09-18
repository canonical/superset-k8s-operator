# Environment Setup

This is part of the
[Charmed Superset Tutorial]().
Please refer to this page for more information and the overview of the content.

## Setting up MicroK8s

Charmed Superset relies on Microk8s or Kubernetes as a container orchestration system.
We'll now set up [MicroK8s](https://microk8s.io/docs) as follows: 

On your machine, install and configure MicroK8s:

```bash
# Install Microk8s from snap:
sudo snap install microk8s --channel 1.25-strict/stable

# Add your user to the MicroK8s group:
sudo usermod -a -G snap_microk8s $USER

# Give your user permissions to read the ~/.kube directory:
sudo chown -f -R $USER ~/.kube

# Create the 'microk8s' group:
newgrp snap_microk8s

# Enable the necessary MicroK8s addons:
sudo microk8s enable hostpath-storage dns

# Set up a short alias for the Kubernetes CLI:
sudo snap alias microk8s.kubectl kubectl
```
Great work! You now have a small Kubernetes cloud on your machine.


## Set up Juju

Juju is an orchestration engine for software operators, which we'll now install and connect to your MicroK8s cloud:

```bash
# Install 'juju':
sudo snap install juju --channel 3.4/stable
# >>> juju (3.5/stable) 3.5.x from Canonical✓ installed

# Since the juju package is strictly confined, you also need to manually create a path:
mkdir -p ~/.local/share

# Register your "microk8s" cloud with juju:
# Not necessary --juju recognises a MicroK8s cloud automatically, as you can see by running 'juju clouds'.
juju clouds
# >>> Cloud      Regions  Default    Type  Credentials  Source    Description
# >>> localhost  1        localhost  lxd   0            built-in  LXD Container Hypervisor
# >>> microk8s   1        localhost  k8s   1            built-in  A Kubernetes Cluster
# (If for any reason this doesn't happen, you can register it manually using 'juju add-k8s microk8s'.)

# Install a "juju" controller into your "microk8s" cloud.
# We'll name ours "superset-controller".
juju bootstrap microk8s superset-controller

# Create a workspace, or 'model', on this controller.
# We'll call ours "superset-model".
# Juju will create a Kubernetes namespace "superset-model"
juju add-model superset-model

# Check status:
juju status
# >>> Model         Controller           Cloud/Region        Version  SLA          Timestamp
# >>> superset-model  superset-controller  microk8s/localhost  3.5.3    unsupported  16:05:03+01:00

# >>> Model "admin/superset-model" is empty.
```

> **See next:
> [Deploying supporting charms]()**