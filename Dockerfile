FROM python:3.12-slim

VOLUME /config
RUN mkdir /server

ENV CONFIG_PATH="/config/"

RUN apt-get update && apt-get install -y \
    protobuf-compiler \
    libglib2.0-0 \
    libgobject-2.0-0 \
    libnspr4 \
    libnss3 \
    libdbus-1-3 \
    libcups2 \
    libexpat1 \
    libxcb1 \
    libxkbcommon0 \
    libatspi2.0-0 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libx11-6 \
    libxcomposite1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libcairo2 \
    libpango-1.0-0 \
    libasound2 \
    fonts-noto-color-emoji \
    fonts-freefont-ttf \
    fonts-unifont \
    fonts-ipafont-gothic \
    fonts-wqy-zenhei \
    fonts-tlwg-loma-otf \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /requirements.txt
RUN pip3 install -r /requirements.txt

# Pre-download CloakBrowser binary so containers start instantly
RUN python3 -c "from cloakbrowser.download import ensure_binary; ensure_binary()"

COPY pcov.proto /pcov.proto
RUN protoc --proto_path=/ --python_out=/server pcov.proto
COPY *.py /server/

ENTRYPOINT ["python3","/server/server.py"]
