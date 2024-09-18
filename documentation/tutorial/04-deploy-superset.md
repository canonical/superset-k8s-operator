# Deploying Charmed Superset

This is part of the
[Charmed Superset Tutorial]().
Please refer to this page for more information and the overview of the content.

Charmed Superset has 3 primary functions:
1. Web server and UI
2. Worker for handling asynchronous queries
3. Beat scheduler for scheduling tasks

For the purposes of our tutorial we will only need the `Web server and UI`.

## Deploy Charmed Superset Server
There are multiple channels of Charmed Superset we can deploy - these can be found on [Charmhub](https://charmhub.io/superset-k8s). If none is specified, `latest/stable` will be deployed.

```bash
juju deploy superset-k8s
```

## Add PostgreSQL and Redis relations
A charm relation is an integration between applications which passes necessary configurations to automate the integration. We'll now add relations between Charmed Superset and Charmed PostgreSQL and Charmed Redis as follows:

```bash
juju relate superset-k8s postgresql-k8s
juju relate superset-k8s redis-k8s
```

You should eventually (approx 5 minutes) see a `juju status --relations` which matches the below. Note that all applications and units are in an active state and relations exist between our charmed superset applications and postgresql and redis applications.

```
Model           Controller           Cloud/Region        Version  SLA          Timestamp
superset-model  superset-controller  microk8s/localhost  3.5.3    unsupported  11:00:56+01:00

App                  Version  Status       Scale  Charm           Channel        Rev  Address         Exposed  Message
postgresql-k8s       14.12    active           1  postgresql-k8s  14/stable      381  10.152.183.243  no       
redis-k8s            7.2.5    active           1  redis-k8s       latest/edge     36  10.152.183.182  no       
superset-k8s                  active           1  superset-k8s    latest/stable   34  10.152.183.135  no       Status check: UP

Unit                    Workload     Agent  Address      Ports  Message
postgresql-k8s/0*       active       idle   10.1.255.10         Primary
redis-k8s/0*            active       idle   10.1.255.21         
superset-k8s/0*         active       idle   10.1.255.5          Status check: UP

Integration provider           Requirer                           Interface          Type     Message
postgresql-k8s:database        superset-k8s:postgresql_db         postgresql_client  regular  
postgresql-k8s:database-peers  postgresql-k8s:database-peers      postgresql_peers   peer     
postgresql-k8s:restart         postgresql-k8s:restart             rolling_op         peer     
postgresql-k8s:upgrade         postgresql-k8s:upgrade             upgrade            peer      
redis-k8s:redis                superset-k8s:redis                 redis              regular  
redis-k8s:redis-peers          redis-k8s:redis-peers              redis-peers        peer        
superset-k8s:peer              superset-k8s:peer                  superset           peer     

```

Congratulations you have deployed Charmed Superset!

> **See next:
> [Creating a Dashboard]()**