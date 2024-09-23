# Cleanup
Remove the juju applications, models and controller to completely cleanup the deployment.

## Remove the Controller
If you’re done with testing and would like to free up resources on your machine, run the following command:

```
juju destroy-controller -y --destroy-all-models --destroy-storage superset-controller
```

[note]
When you remove the models, all the data in PostgreSQL and any other applications inside the model are lost.
[\note]

## Next Steps
- [Prepare for a production deployment of Charmed Superset](../how-to/prepare-for-production.md)
- [Explore Apache Superset documentation](https://superset.apache.org/docs/intro)
- [Join the Apache Superset Slack community](https://apache-superset.slack.com/)
- [Report any problems you've encountered](https://github.com/canonical/superset-k8s-operator/issues)
- [Contribute to the code base](https://github.com/canonical/superset-k8s-operator/blob/main/CONTRIBUTING.md)
