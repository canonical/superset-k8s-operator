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

- Scale Charmed Superset
- Observe Charmed Superset with Cos-lite
- Secure Charmed Superset with Google Oauth
- Explore Apache Superset documentation
- Join the Apache Superset Slack community
- Report any problems you've encountered
- Contribute to the code base
