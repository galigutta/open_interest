
The Dockerfile has the steps to build a container for AWS Container Registry (ECR). This has been done initially, but if any additional pip installs are needed, the docker image needs to be rebuilt after requirements.txt is updated.

When the container runs, it updates the repo, but the only thing that matters is changes to oi.py get loaded automatically.

There are a bunch of files that can be cleaned up here as most of the needed artifacts are in S3 so.csv mainly.

Also, note CMD does not execute anything at build time, but specifies the intended command for the image. So this can be edited if some other dependencies, etc., are needed in a pinch, though it is better to bake it into the image itself
