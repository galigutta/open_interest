FROM python

RUN echo "#!/bin/bash\n" > /startscript.sh
RUN echo "apt update" > /startscript.sh
RUN echo "apt-get update && apt-get install -y git" > /startscript.sh
RUN echo "git clone https://github.com/galigutta/open_interest\n" >> /startscript.sh
RUN echo "pip install -r open_interest/requirements.txt" >> /startscript.sh

RUN chmod +x /startscript.sh
RUN /startscript.sh
WORKDIR /open_interest

CMD python oi.py
