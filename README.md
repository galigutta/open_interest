
The Dockerfile has the steps to build a container for AWS Container Registry (ECR). This has been done initially, but if any additional pip installs are needed, the docker image needs to be rebuilt after requirements.txt is updated. (Use --no-cache option to force include any changes to requirements.txt. Amazon ECR has handy commands for pushing the new image to repo. It might be needed to substitute sudo docker for docker in some commands)

When the container runs, it updates the repo, but the only thing that matters is changes to oi.py get loaded automatically.

Cleaned up the files, but the empty snapsot directory is needed to read the artifacts from S3, though this could be done away with.

Also, note CMD does not execute anything at build time, but specifies the intended command for the image. So this can be edited if some other dependencies, etc., are needed in a pinch, though it is better to bake it into the image itself
