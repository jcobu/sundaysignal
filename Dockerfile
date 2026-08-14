FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install --no-cache-dir \
    requests==2.32.3 \
    beautifulsoup4==4.12.3 \
    lxml==5.3.0 \
    flask==3.0.3

COPY sundaysignal_scraper.py espn_schedule.py serve.py webapp.py entrypoint-crawler.sh ./
COPY static ./static
RUN chmod +x entrypoint-crawler.sh && mkdir -p /output

# Default: continuous crawler. Other services override command.
CMD ["./entrypoint-crawler.sh"]
