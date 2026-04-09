# Contributing

To make contributions to this charm, you'll need a working [development setup](https://juju.is/docs/sdk/dev-setup).

A lot of the commands you would need are covered with the [Makefile](./Makefile), learn more by running `make help`.

**Note:** It is recommended to build in the host and deploy in a [Multipass](https://canonical.com/multipass/install) instance.
Use `multipass mount` to mount the project directory with the build artifacts into your Multipass instance.

## Environment for coding

You can install the dependencies for coding with:

```shell
make install-build-deps
```

**Note:** If you run this from the VS Code snap's integrated terminal you may get a weird PATH. Run it from an external terminal instead.

You can create an environment for coding with:

```shell
make venv
source venv/bin/activate
```

## Environment for building

You can install the dependencies for building with:

```shell
make install-build-deps
```

This installs: LXD, charmcraft, rockcraft, yq, uv, tox, and the skopeo alias.

After installation, LXD must be initialized:

```shell
sudo adduser $USER lxd
newgrp lxd
lxd init --auto
```

## Verify build environment

You can verify that you have all the necessary dependencies installed with:

```shell
make check-build-deps
```

## Building artifacts

You can build the charm with:

```shell
make build-charm
```

You can build the rock with:

```shell
make build-rock
```

## Code quality

You can run linters, static analysis, and unit tests with:

```shell
make fmt     # Runs formatters and also does formatting checks
make lint    # Runs linters
make test    # Runs static analysis and unit tests
make checks  # Runs all of the above

make test-integration  # Runs integration tests*
```

*: It is recommended to let CI runners run integration tests on GitHub Actions.

## Deploying locally

### Environment setup

You can install the dependencies with:

```shell
make install-deploy-deps
```

This installs: Juju, MicroK8s, and Docker.

After installation, some additional setup is required:

```shell
# Enable required MicroK8s addons
sudo microk8s enable hostpath-storage
sudo microk8s enable registry

# Add your user to the required groups
sudo usermod -aG snap_microk8s $USER
sudo groupadd docker
sudo usermod -aG docker $USER

sudo snap disable docker
sudo snap enable docker

# Both microk8s and docker require new groups
# and `newgrp` does not cover for both at the same time.
# A system reboot is recommended at this point.

# Create the Juju home directory before bootstrapping
# (required when running from a snap-confined terminal such as VS Code)
mkdir -p ~/.local/share/juju

juju bootstrap microk8s
```

### Deployment

You can deploy Superset's UI server using local artifacts with:

```shell
make import-rock
make deploy-local
```

Superset runs as three separate components. After the initial deploy you can add the worker and beat scheduler:

```shell
# Deploy a worker
juju deploy ./superset-k8s_amd64.charm --resource superset-image=localhost:32000/superset:latest --config charm-function=worker superset-k8s-worker

# Deploy the beat scheduler
juju deploy ./superset-k8s_amd64.charm --resource superset-image=localhost:32000/superset:latest --config charm-function=beat superset-k8s-beat
```

It is recommended to change the logging configuration when working with deployments:

```shell
juju model-config logging-config="<root>=INFO;unit=DEBUG"
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

### Trino
When related to Trino, Superset automatically creates database connections for Trino catalogs and grants their access to the default user role.

Superset applies a partial reconciliation between its database connections and the available Trino catalogs by: 
- adding connections for catalogs that don't have one already
- updating the connection strings if the Trino URL or credentials are updated 
- not updating other connection options or permissions if they are changed from the defaults
- not removing database connections when catalogs are removed or the relation to Trino is broken

For the relation to work, a user named `app-superset-k8s` has to be added in Trino's `trino-user-management` secret and Superset should be granted access to the secret.

```
# deploy trino charm
juju deploy trino-k8s --config charm-function=all --trust

# relate trino charm
juju relate trino-k8s superset-k8s-ui

# grant access to trino credentials secret
juju grant-secret trino-user-management superset-k8s-ui

# remove relation
juju remove-relation trino-k8s superset-k8s-ui

# remove application
juju remove-application trino-k8s
```
This relation makes use of the `trino_k8s.v0.trino_catalog` library. The charm can be found on [Charmhub](https://charmhub.io/trino-k8s) and on [github](https://github.com/canonical/trino-k8s-operator).

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

```shell
# Remove the application before retrying
juju remove-application superset-k8s-ui superset-k8s-beat superset-k8s-worker --force
```

## Upgrading Superset version

The Apache Superset project uses [semantic versioning](https://semver.org/). This charmed operator is set up to utilize the same versioning. However, the [charmed operator](https://charmhub.io/superset-k8s) uses channels (`edge` and `stable`) to differentiate between major versions and risk levels (e.g. channel `5/stable` is used to deploy Superset 5, while `6/stable` is used to deploy Superset 6). Upgrades between major stable tracks should result in no breaking changes.

On Github, the `main` branch corresponds to the `latest/edge` channel on Charmhub. The `track/*` branches correspond to the `*/edge` channels on Charmhub. Depending on which channel you are targeting, you should make the changes on the relevant Github branch.

The following are steps required to perform a major upgrade on the Superset charmed operator. For simplicity, we will assume we are upgrading Superset to `v6`.

## Pull changes from most recent track

As an example, if you are upgrading to version 6, then you must pull the latest changes from the most recent stable version (in this case, it would be branch `track/5` on Github). Resolve any merge conflicts that may come up. This will ensure any changes or bug fixes made to the charm on `track/5` are propagated to the latest version.

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
