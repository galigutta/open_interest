
The Dockerfile has the steps to build a container for AWS Container Registry (ECR). This has been done initially, but if any additional pip installs are needed, the docker image needs to be rebuilt after requirements.txt is updated.

When the container runs, it updates the repo, but the only thing that matters is changes to oi.py get loaded automatically.

There are a bunch of files that can be cleaned up here as most of the needed artifacts are in S3 so.csv mainly.
