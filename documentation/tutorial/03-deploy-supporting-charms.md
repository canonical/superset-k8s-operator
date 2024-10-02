# Deploy supporting charms

This part of the tutorial focuses on deploying PostgreSQL and Redis charms, which Superset requires to function.

## Deploy a Database
Charmed Superset relies on an external [Charmed PostgreSQL](https://charmhub.io/postgresql-k8s) database for storing application metadata such as users, dashboard definitions and logs. Deploy it as follows:

```bash
juju deploy postgresql-k8s --trust

```
You can check the deployment was successful by running `juju status`. You should expect an output like this:

```
Model           Controller           Cloud/Region        Version  SLA          Timestamp
superset-model  superset-controller  microk8s/localhost  3.5.3    unsupported  10:48:10+01:00

App             Version  Status   Scale  Charm           Channel    Rev  Address  Exposed  Message
postgresql-k8s           waiting    0/1  postgresql-k8s  14/stable  381           no       installing agent

Unit              Workload  Agent       Address  Ports  Message
postgresql-k8s/0  waiting   allocating                  installing agent

```

[note]
The database deployment may take some time, approximately 10 minutes, to complete. After that, all Juju components should be in `active` status. 
[/note]

## Deploy a cache and message queue
Charmed Superset relies on an external [Charmed Redis](https://charmhub.io/redis-k8s) deployment, which acts as both a cache and message queue for Superset Celery workers. Deploy it as follows:

```bash
juju deploy redis-k8s --channel=edge
```
Check the deployment with `juju status --watch 2s`, with focus on the `redis-k8s` application and `redis-k8s/0` unit. The deployment is completed once all units reach the `active` status:

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

**See next:
[Deploy Charmed Superset](https://discourse.charmhub.io/t/deploy-charmed-superset/15644)**