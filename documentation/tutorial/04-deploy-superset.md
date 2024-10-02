# Deploy Charmed Superset

This part of the tutorial focuses on deploying Charmed Superset and adding relations to supporting charms.

Charmed Superset has three primary functions:
1. Web server and User Interface (UI).
2. Worker for handling asynchronous queries.
3. Beat scheduler for scheduling tasks.

This tutorial only focuses on the web server and UI.

## Deploy Charmed Superset server
There are multiple channels of Charmed Superset you can deploy. See [Charmhub](https://charmhub.io/superset-k8s) for more details. If none is specified, `latest/stable` is deployed.

```bash
juju deploy superset-k8s
```

## Add PostgreSQL and Redis relations
A charm relation is an integration between applications which passes necessary configurations to automate the integration. You can add relations between Charmed Superset, Charmed PostgreSQL and Charmed Redis as follows:

```bash
juju relate superset-k8s postgresql-k8s
juju relate superset-k8s redis-k8s
```

[note]
This relation addition takes approximately 5 minutes to complete.
[/note]

When running `juju status --relations`, you should expect an output like the following:

```bash
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
Note that all applications and units are in active state and relations exist between our Charmed Superset applications and PostgreSQL and Redis applications.

**See next:
[Create a Dashboard](https://discourse.charmhub.io/t/create-a-dashboard/15645)**