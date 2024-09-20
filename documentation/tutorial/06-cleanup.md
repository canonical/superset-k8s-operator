# Cleanup

This is part of the
[Charmed Superset Tutorial]().
Please refer to this page for more information and the overview of the content.

In this tutorial, we have successfully deployed Charmed Superset UI and created our first dashboard!

## Removing Charmed Superset
If you’re done with testing and would like to free up resources on your machine, just run the following command:

```
juju destroy-controller -y --destroy-all-models --destroy-storage superset-controller
```

Warning: when you remove the models as shown, you will lose all the data in PostgreSQL and any other applications inside the model!

## Next Steps
Wondering what to do next, take a look at some suggestions below:

- [Prepare for a production deployment of Charmed Superset](../how-to/prepare-for-production.md)
- [Explore Apache Superset documentation](https://superset.apache.org/docs/intro)
- [Join the Apache Superset Slack community](https://apache-superset.slack.com/)
- [Report any problems you've encountered](https://github.com/canonical/superset-k8s-operator/issues)
- [Contribute to the code base](https://github.com/canonical/superset-k8s-operator/blob/main/CONTRIBUTING.md)
