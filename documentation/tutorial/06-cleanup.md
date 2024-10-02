# Cleanup
This final step of the tutorial focuses on removing Juju applications, models and controller to completely cleanup the deployment.

## Remove the Controller

If you’re done with testing and would like to free up resources on your machine, run the following command:

```
juju destroy-controller -y --destroy-all-models --destroy-storage superset-controller
```

[note]

When you remove the models, all the data in PostgreSQL and any other applications inside the model are lost.

[/note]

## Next Steps

- [Integrate Canonical Observability Stack](https://discourse.charmhub.io/t/observe-key-performance-metrics/15650).
- [Implement security features](https://discourse.charmhub.io/t/enable-security-features/15649).
- [Optimise deployment performance](https://discourse.charmhub.io/t/optimise-your-deployment-performance/15651).
- [Learn which feature flags are supported](https://discourse.charmhub.io/t/supported-feature-flags/15647).