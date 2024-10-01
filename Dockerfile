FROM alpine:3.20

VOLUME /config
RUN mkdir /server

ENV CONFIG_PATH="/config/"
ENV CHROME_DRIVER="/usr/bin/chromedriver"

RUN echo "http://dl-cdn.alpinelinux.org/alpine/edge/community" > /etc/apk/repositories
RUN echo "http://dl-cdn.alpinelinux.org/alpine/edge/main" >> /etc/apk/repositories
RUN apk update

RUN apk add chromium
RUN apk add chromium-chromedriver
RUN apk add python3
RUN apk add py3-pip
RUN apk add protoc

RUN rm -rf /var/lib/apt/lists/*

COPY requirements.txt /requirements.txt
RUN pip3 install --break-system-packages -r /requirements.txt

COPY pcov.proto /pcov.proto
RUN /usr/bin/protoc --proto_path=/ --python_out=/server pcov.proto
COPY *.py /server/

ENTRYPOINT ["python3","/server/server.py"]
