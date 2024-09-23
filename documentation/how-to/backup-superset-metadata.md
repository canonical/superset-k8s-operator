## Enabling Database Backups
Superset relies on PostgreSQL for metadata storage. You can enable database backups by integrating the PostgreSQL charm with the S3 integrator. Configure it to store backups in an S3-compatible bucket, ensuring disaster recovery and data retention.

See [Configure S3 for AWS](https://charmhub.io/postgresql-k8s/docs/h-configure-s3-aws) for more details.