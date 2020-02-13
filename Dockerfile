FROM ubuntu
#You can start with any base Docker Image that works for you

RUN echo "#!/bin/bash\n" > /startscript.sh
RUN echo "mkdir github\n" >> /startscript.sh
RUN echo "cd github\n" >> /startscript.sh
RUN echo "git clone https://github.com/galigutta/open_interest\n" >> /startscript.sh
RUN echo "cd *\n" >> /startscript.sh
RUN echo "echo from start script" >> /startscript.sh
RUN echo "ls"

RUN chmod +x /startscript.sh

CMD /startscript.sh