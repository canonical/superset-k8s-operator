# Deploy Supporting Charms

This is part of the
[Charmed Superset Tutorial]().
Please refer to this page for more information and the overview of the content.

## Deploying a Database
Charmed Superset relies on an external Charmed PostgreSQL database for storing application metadata such as users, dashboard definitions and logs.


```bash
# Deploy Charmed PostgreSQL
juju deploy postgresql-k8s --trust
# >>> Deployed "postgresql-k8s" from charm-hub charm "postgresql-k8s", revision 381 in channel 14/stable on ubuntu@22.04/stable

```
You can check the deployment was successful by running `juju status`.

```
Model           Controller           Cloud/Region        Version  SLA          Timestamp
superset-model  superset-controller  microk8s/localhost  3.5.3    unsupported  10:48:10+01:00

App             Version  Status   Scale  Charm           Channel    Rev  Address  Exposed  Message
postgresql-k8s           waiting    0/1  postgresql-k8s  14/stable  381           no       installing agent

Unit              Workload  Agent       Address  Ports  Message
postgresql-k8s/0  waiting   allocating                  installing agent

```

This may take some time (up to 10 minutes) to get to an `active` status. In the meantime we can move on..

## Deploying a Cache and Message Queue
Charmed Superset relies on an external Charmed Redis deployment, which acts as both a cache and message queue for Superset Celery workers.

We'll deploy this now:
```bash
# Deploy Charmed PostgreSQL
juju deploy redis-k8s --channel=edge
# >>> Deployed "redis-k8s" from charm-hub charm "redis-k8s", revision 36 in channel latest/edge on ubuntu@22.04/stable

```
You can check the deployment has been successful by running `juju status --watch 2s` and taking note of the `redis-k8s` application and `redis-k8s/0` unit. Once all units reach an `Active` status we can proceed.

```
Model           Controller           Cloud/Region        Version  SLA          Timestamp
superset-model  superset-controller  microk8s/localhost  3.5.3    unsupported  10:51:29+01:00

App             Version  Status  Scale  Charm           Channel      Rev  Address         Exposed  Message
postgresql-k8s  14.12    active      1  postgresql-k8s  14/stable    381  10.152.183.243  no       
redis-k8s       7.2.5    active      1  redis-k8s       latest/edge   36  10.152.183.182  no       

Unit               Workload  Agent  Address      Ports  Message
postgresql-k8s/0*  active    idle   10.1.255.10         Primary
redis-k8s/0*       active    idle   10.1.255.21    
```

> **See next:
> [Deploying Charmed Superset]()**