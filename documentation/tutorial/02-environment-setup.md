# Setup your environment

You will now be installing Microk8s and Juju, which are requirements of any charmed deployment.

## Set up MicroK8s

Charmed Superset relies on Kubernetes (K8s) as a container orchestration system.
For this tutorial, you will use [MicroK8s](https://microk8s.io/docs), a lightweight distribution of K8s. 

Install MicroK8s and provide your user with the required permissions. This can be achieved by adding it to the `snap_microk8s` group and giving permissions to the `~/.kube` directory.

```bash
sudo snap install microk8s --channel 1.25-strict/stable
newgrp snap_microk8s
sudo usermod -a -G snap_microk8s $USER
sudo chown -f -R $USER ~/.kube
```

You can now enable the necessary MicroK8s addons, as follows:
```bash
sudo microk8s enable hostpath-storage dns
```
For ease you should set up a short alias for the Kubernetes CLI with:
```bash
sudo snap alias microk8s.kubectl kubectl
```
You now have a small Kubernetes cloud on your machine.


## Set up Juju

Charmed Superset uses Juju as the orchestration engine for software operators. Install and connect it to your MicroK8s cloud with the following steps.

Firstly, install `juju` from a snap, with channel at least 3.4 or above.
```bash
sudo snap install juju --channel 3.5/stable
```
Since the juju package is strictly confined, you also need to manually create a path:
```bash
mkdir -p ~/.local/share
```
Juju recognises a MicroK8s cloud automatically, as you can see by running 'juju clouds'. If for any reason this doesn't happen, you can register it manually using 'juju add-k8s microk8s'.
```bash
# >>> Cloud      Regions  Default    Type  Credentials  Source    Description
# >>> localhost  1        localhost  lxd   0            built-in  LXD Container Hypervisor
# >>> microk8s   1        localhost  k8s   1            built-in  A Kubernetes Cluster
```
Next, install a "juju" controller into your "microk8s" cloud. We'll name ours "superset-controller". 

```bash
juju bootstrap microk8s superset-controller
```

Finally, create a workspace, or 'model', on this controller. We'll call ours "superset-model". Juju will create a Kubernetes namespace "superset-model"
```bash
juju add-model superset-model
```
After this you should see something similar to the below when running the `juju status` command.
```bash
# >>> Model           Controller           Cloud/Region        Version  SLA          Timestamp
# >>> superset-model  superset-controller  microk8s/localhost  3.5.3    unsupported  16:05:03+01:00

# >>> Model "admin/superset-model" is empty.
```

**See next:
[Deploy supporting charms](02-environment-setup.md)**