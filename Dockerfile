FROM anasty17/mltb:latest

# OOKLA_SPEEDTEST_BEGIN
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
    && curl -fL --retry 3 --retry-delay 2 \
        -o /tmp/speedtest.deb \
        "https://packagecloud.io/ookla/speedtest-cli/packages/debian/trixie/speedtest_1.2.0.84-1.ea6b6773cf_amd64.deb/download.deb?distro_version_id=221" \
    && echo "35e084567a6388631fb10cf01e5e0d6b57a67d34ede2b72ba111b3d9164c8b94  /tmp/speedtest.deb" \
        | sha256sum -c - \
    && apt-get install -y --no-install-recommends \
        /tmp/speedtest.deb \
    && rm -f /tmp/speedtest.deb \
    && rm -rf /var/lib/apt/lists/*
# OOKLA_SPEEDTEST_END


WORKDIR /app
RUN chmod 777 /app

RUN python3 -m venv mltbenv

COPY requirements.txt .
RUN mltbenv/bin/pip install --no-cache-dir -r requirements.txt

RUN mltbenv/bin/pip install --no-cache-dir -U --pre "yt-dlp[default,curl-cffi]"

COPY . .

RUN sed -i 's/\r$//' *.sh

CMD ["bash", "start.sh"]
