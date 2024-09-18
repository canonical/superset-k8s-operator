# Compatible Charm Revisions and Resources

Deploying charms from the `latest/stable` channel *should* guarantee 
compatibility between charm revision and resource.

However, if you are deploying earlier revisions of the charm please
use the below table as a reference for compatibility.


| Revision | Resource revision | Resource Version |
|----------|-------------------|------------------|
| 34       | 18                | 3.1.3            |
| 33       | 18                | 3.1.3            |
| 32       | 15                | 3.1.3            |
| 31       | 14                | 2.1.0            |

A revision and resource version can be specified in the deployment:

```
juju deploy superset-k8s --channel=stable --revision=31 --resource superset-image=14
```
