# Testing

This project uses `tox` for managing test environments (4.4.x). There are some pre-configured environments
that can be used for linting and formatting code when you're preparing contributions to the charm:

```shell
tox run -e fmt           # update your code according to linting rules
tox run -e lint          # code style
tox run -e unit          # unit tests
tox run -e integration   # integration tests
tox                      # runs 'format', 'lint', and 'unit' environments
```

# Deploy Superset

This charm is used to deploy Superset Server in a k8s cluster. For local deployment, follow the following steps:

## Set up your development environment with Multipass [~ 10 mins]
When you’re trying things out, it’s good to be in an isolated environment, so you don’t have to worry too much about cleanup. It’s also nice if you don’t need to bother too much with setup. In the Juju world you can get both by spinning up an Ubuntu virtual machine (VM) with Multipass, specifically, using their Juju-ready `charm-dev` blueprint.
```
sudo snap install multipass
multipass launch --cpus 4 --memory 8G --disk 30G --name superset-vm charm-dev
```
That's it! Your dependencies are all set up, please run `multipass shell superset-vm` and skip to `Create a model`.

## Set up your development environment without Multipass blueprint
### Install Microk8s
```
# Install microk8s from snap:
sudo snap install microk8s --channel=1.27-strict/stable

# Setup an alias for kubectl:
sudo snap alias microk8s.kubectl kubectl

# Add your user to the Microk8s group:
sudo usermod -a -G snap_microk8s $USER

# Switch to the 'microk8s' group:
newgrp snap_microk8s

# Wait for microk8s to be ready:
microk8s status --wait-ready

# Enable the necessary Microk8s addons:
sudo microk8s.enable dns 
sudo microk8s.enable rbac 
sudo microk8s.enable hostpath-storage

# Wait for addons to be rolled out:
microk8s.kubectl rollout status deployments/coredns -n kube-system -w --timeout=600s
microk8s.kubectl rollout status deployments/hostpath-provisioner -n kube-system -w --timeout=600s
```
### Install Charmcraft
```
# Install lxd from snap:
sudo snap install lxd --classic --channel=5.0/stable

# Install charmcraft from snap:
sudo snap install charmcraft --classic --channel=latest/stable

# Charmcraft relies on LXD. Configure LXD:
lxd init --auto
```
### Set up the Juju OLM
```
# Install the Juju CLI client, juju:
sudo snap install juju --channel=3/stable

# Make Juju directory
mkdir -p ~/.local/share/juju

# Install a "juju" controller into your "microk8s" cloud:
juju bootstrap microk8s superset-controller
```
### Create a model
```
# Create a 'model' on this controller:
juju add-model superset-k8s

# Enable DEBUG logging:
juju model-config logging-config="<root>=INFO;unit=DEBUG"

# Check progress:
juju status
juju debug-log
```
### Configure juju
This is an optional step intended to make the update-status hook more responsive. The default value is 5m.
Following deployment, the status of the application will be checked at this regular interval. By setting this to 1m the deployment will be able to reach an `Active` status soon after application start. Leaving this at 5m will require waiting 5 minutes following deployment for application verification.

Be aware if you update this configuration on the model it will apply to all charms on that model.

```
# customise update status hook interval:
juju model-config update-status-hook-interval=1m
```

### Pack rock
```
cd superset_rock

rockcraft pack
rockcraft.skopeo --insecure-policy copy --dest-tls-verify=false oci-archive:superset_<version>-24.04-edge_amd64.rock docker://localhost:32000/superset-rock:<version>
```

### Deploy charm
```
cd ../superset-k8s-operator

# Pack the charm:
charmcraft pack

# deploy the web server
juju deploy ./superset-k8s_ubuntu-22.04-amd64.charm --resource superset-image=localhost:32000/superset-rock:<version> superset-k8s-ui

# deploy a worker
juju deploy ./superset-k8s_ubuntu-22.04-amd64.charm --resource superset-image=localhost:32000/superset-rock:<version> --config charm-function=worker superset-k8s-worker

# deploy the beat scheduler
juju deploy ./superset-k8s_ubuntu-22.04-amd64.charm --resource superset-image=localhost:32000/superset-rock:<version> --config charm-function=beat superset-k8s-beat

# Check deployment was successful:
juju status
```
## Relations
### Redis
Redis acts as both a cache and message broker for Superset services. It's a requirement to have a redis relation in order to start the Superset application.
```
# deploy redis charm
juju deploy redis-k8s --channel edge

# relate redis charm
juju relate redis-k8s superset-k8s-ui

# remove relation
juju remove-relation redis-k8s superset-k8s-ui

# remove application
juju remove-application redis-k8s
```
The recommended method for relating applications to the Redis Charm is using the `redis-k8s.v0.redis` library, and utilising stored state for accessing relation data.

### PostgreSQL
PostgreSQL is used as the database that stores Superset metadata (slices, connections, tables, dashboards etc.). It's a requirement to have a PostgreSQL relation to start the Superset application.
```
# deploy postgresql charm
juju deploy postgresql-k8s --channel 14/stable

# relate postgresql charm
juju relate postgresql-k8s superset-k8s-ui

# remove relation
juju remove-relation postgresql-k8s superset-k8s-ui

# remove application
juju remove-application postgresql-k8s
```
This relation makes use of the `data_platform_libs.v0.data_interfaces` library. The charm can be found on [Charmhub](https://charmhub.io/postgresql-k8s) and on [github](https://github.com/canonical/postgresql-k8s-operator).

### Authentication
To validate Google Oauth authentication:
```
# Port forward the web server
kubectl port-forward pod/superset-k8s-0 8088:8088 -n superset-k8s

```
You can then follow instructions in the [README.md](README.md) to set up the Google `redirect_uri` to  `http://localhost:8088/oauth-authorized/google`.

Please note: `redirect_uri` should be updated to `https://<host>/oauth-authorized/google` when deploying to production.

Once you have authenticated with Google, to verify the user credentials that have been created you can access these through the PostgreSQL charm as follows:
```
# Get the postgresql password
juju run postgresql-k8s/leader get-password

# Make note of the postgresql unit IP
juju status

# SSH into the application
juju ssh --container postgresql postgresql-k8s/leader bash

# Use psql as the postgres user
psql --host=<unit ip> --username=operator --password postgres

# Connect to the superset database
\c superset

# Verify the credentials created for your user
SELECT * FROM ab_user WHERE email = '<your email>';

```

## Cleanup
# Remove the application before retrying
```
juju remove-application superset-k8s-ui superset-k8s-beat superset-k8s-worker --force
```

# Upgrading Superset version

The Apache Superset project uses [semantic versioning](https://semver.org/). This charmed operator is set up to utilize the same versioning. However, the [charmed operator](https://charmhub.io/superset-k8s) uses channels (`edge` and `stable`) to differentiate between major versions and risk levels (e.g. channel `5/stable` is used to deploy Superset 5, while `6/stable` is used to deploy Superset 6). Upgrades between major stable tracks should result in no breaking changes.

On Github, the `main` branch corresponds to the the `latest/edge` channel on Charmhub. The `track/*` branches correspond to the `*/edge` channels on Charmhub. Depending on which channel you are targeting, you should make the changes on the relevant Github branch.

The following are steps required to perform a major upgrade on the Superset charmed operator. For simplicity, we will assume we are upgrading Superset to `v6`.

## Pull changes from most recent track

As an example, if you are upgrading to version 6, then you must pull the latest changes from the most recent stable version (in this case, it would be branch `track/5` on Github). Resolve any merge conflicts that may come up. This will ensure any changes or bug fixes made to the charm on `track/5` are propogataed to the latest version.

## Upgrade Superset rock

The [`superset_rock`](./superset_rock/) directory contains the [`rockcraft.yaml`](./superset_rock/rockcraft.yaml) file that contains the source build definition. Here, we need to update the source to reflect the version being built (e.g. `6.0.0`), as well as updating the checksums. Depending on upstream changes, this may entail updating build dependencies in different parts of the rock.

## Upgrade Superset charm

Depending on the changes introduced with the new version of Superset, we may need to update certain aspects of the Superset charm, such as adding/removing feature flags or updating dependencies such as Redis. An example of this can be found [here](https://github.com/canonical/superset-k8s-operator/pull/64/changes).

## Create new track on Charmhub

If a channel for the new version does not exist on Charmhub, you must request for it to be created. This can be done by creating a new topic on [Discourse](https://discourse.charmhub.io/) requesting the creation of such tracks. In this case, you should request the creation of channels `6/edge` and `6/stable`.

## Update major_upgrades integration test

The purpose of the [`test_major_upgrades.py`](./tests/integration/test_major_upgrades.py) is to test the compatibility of upgrading the charm between major versions of Superset. To do so, we must modify this test to deploy the stable version of the previous release (e.g. `5/stable` if we are attempting to upgrade to Superset v6). The success of this test ensures there are no breaking changes when performing major upgrades with the Superset charm.

## Test and promote charm

Once the PR is successfully merged to the `main` branch, it will automatically release to the `latest/edge` channel on Charmhub. You must test this on a staging environment to ensure the charm is functional. Once confirmed, you may use the [Promote charm](https://github.com/canonical/superset-k8s-operator/actions/workflows/promote_charm.yaml) workflow to promote it from `latest/edge` to `6/edge`.

Once the charm is thoroughly tested on staging and deemed production-ready, the promote charm workflow should be triggered again to promote the channel `6/edge` to `6/stable`.

## Create track/<x> branch

Once the PR is successfully merged to the `main` branch, create a new `track/<x>` track which will be used to push changes to the `<x>/edge` channel on Charmhub. To do so, run the following (the below is an example for `track/6` branch):

```shell
git checkout main
git pull origin main

git checkout -b track/6
git push -u origin track/6
```

Once done, a repo admin **must** create branch protection rules for the newly created branch.
