# Cleanup
This final step of the tutorial focuses on removing Juju applications, models and controller to completely cleanup the deployment.

## Remove the Controller
If you’re done with testing and would like to free up resources on your machine, run the following command:

```
juju destroy-controller -y --destroy-all-models --destroy-storage superset-controller
```

[note]
When you remove the models, all the data in PostgreSQL and any other applications inside the model are lost.
[\note]

## Next Steps
- [Integrate Canonical Observability Stack](../how-to/observe-superset-metrics.md)
- [Implement security features](../how-to/enable-superset-security-features.md)
- [Optimize deployment performance](../how-to/optimize-deployment-performance.md)
- [Learn which feature flags are supported](../reference/feature-flags.md)
