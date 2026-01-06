# Compatible Charm revisions and resources

Deploying charms from the `*/stable` channels *should* guarantee 
compatibility between charm revision and resource. For example, you may deploy the charm from the `5/stable` channel to deploy Superset v5, and upgrade to the `6/stable` channel to upgrade to Superset v6 by running:

```
juju refresh superset-k8s --channel=6/stable
```

However, if you are deploying earlier revisions of the charm,
use the table below as a reference for compatibility when using the `latest/stable` channel.


| Revision | Resource revision | Resource version |
|----------|-------------------|------------------|
| 40       | 21                | 3.1.3            |
| 34       | 18                | 3.1.3            |
| 33       | 18                | 3.1.3            |
| 32       | 15                | 3.1.3            |
| 31       | 14                | 2.1.0            |

You can specify both charm and resource revision in the deployment. See the following example:

```
juju deploy superset-k8s --channel=stable --revision=31 --resource superset-image=14
```
