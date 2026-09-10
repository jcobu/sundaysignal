FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install --no-cache-dir \
    requests==2.32.3 \
    beautifulsoup4==4.12.3 \
    lxml==5.3.0 \
    flask==3.0.3 \
    "pysocks==1.7.1"

COPY sundaysignal_scraper.py espn_schedule.py serve.py webapp.py entrypoint-crawler.sh ./
COPY static ./static
# Strip any CRLF line endings (e.g. from a Windows checkout or editor) so the
# shebang resolves — CRLF here otherwise fails as a baffling "exec: no such
# file or directory" even though the file is clearly present.
RUN sed -i 's/\r$//' entrypoint-crawler.sh \
    && chmod +x entrypoint-crawler.sh \
    && mkdir -p /output

# Default: continuous crawler. Other services override command.
CMD ["./entrypoint-crawler.sh"]
