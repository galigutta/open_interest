FROM python

RUN echo "#!/bin/bash\n" > /startscript.sh
RUN echo "apt update" > /startscript.sh
RUN echo "apt-get update && apt-get install -y git" > /startscript.sh
RUN echo "git clone https://github.com/galigutta/open_interest\n" >> /startscript.sh
RUN echo "pip install -r open_interest/requirements.txt" >> /startscript.sh

RUN chmod +x /startscript.sh
RUN /startscript.sh

ENV TZ=America/New_York
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

WORKDIR /open_interest

CMD ["sh","-c","git pull origin master && python oi.py"]