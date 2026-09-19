FROM python:3.14-slim

VOLUME /config
RUN mkdir /server

ENV CONFIG_PATH="/config/"

RUN apt-get update && apt-get install -y \
    protobuf-compiler \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /requirements.txt
RUN pip3 install -r /requirements.txt

COPY pcov.proto /pcov.proto
RUN protoc --proto_path=/ --python_out=/server pcov.proto
COPY *.py /server/

ENTRYPOINT ["python3","/server/server.py"]
