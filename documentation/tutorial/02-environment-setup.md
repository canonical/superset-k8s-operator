# Setup your environment

This part of the tutorial focuses on how to set up your environment and install the required dependencies.

## Set up MicroK8s

Charmed Superset relies on Kubernetes (K8s) as a container orchestration system.
For this tutorial, you will use [MicroK8s](https://microk8s.io/docs), a lightweight distribution of K8s. 

Install MicroK8s and provide your user with the required permissions. You can do so by adding it to the `snap_microk8s` group and giving permissions to the `~/.kube` directory:

```bash
sudo snap install microk8s --channel 1.25-strict/stable
newgrp snap_microk8s
sudo usermod -a -G snap_microk8s $USER
sudo chown -f -R $USER ~/.kube
```

Enable now the necessary MicroK8s add-ons as follows:
```bash
sudo microk8s enable hostpath-storage dns
```
For ease, you can set up a short alias for the Kubernetes CLI with:
```bash
sudo snap alias microk8s.kubectl kubectl
```

## Set up Juju

Charmed Superset uses Juju as the orchestration engine for software operators. Install and connect it to your MicroK8s cloud with the following steps.

Firstly, install `juju` from a snap:
```bash
sudo snap install juju --channel 3.5/stable
```

[note]
This charm requires juju with channel >= 3.4.
[/note]

Since the Juju package is strictly confined, you also need to manually create a path:
```bash
mkdir -p ~/.local/share
```
Juju recognises a MicroK8s cloud automatically, as you can see by running 'juju clouds':
```bash
# >>> Cloud      Regions  Default    Type  Credentials  Source    Description
# >>> localhost  1        localhost  lxd   0            built-in  LXD Container Hypervisor
# >>> microk8s   1        localhost  k8s   1            built-in  A Kubernetes Cluster
```
If for any reason your MicroK8s is not recognised, register it manually using 'juju add-k8s microk8s'.

Next, install a Juju controller into your MicroK8s cloud. For this example, the controller is named "superset-controller":

```bash
juju bootstrap microk8s superset-controller
```

Finally, create a workspace, or 'model', on this controller. For this example, the workspace is named "superset-model". Juju will create a Kubernetes namespace "superset-model":
```bash
juju add-model superset-model
```
After this, you should see something similar to the below when running the `juju status` command:
```bash
# >>> Model           Controller           Cloud/Region        Version  SLA          Timestamp
# >>> superset-model  superset-controller  microk8s/localhost  3.5.3    unsupported  16:05:03+01:00

# >>> Model "admin/superset-model" is empty.
```

**See next:
[Deploy supporting charms](02-environment-setup.md)**