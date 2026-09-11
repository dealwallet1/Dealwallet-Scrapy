FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app

WORKDIR /app

# System dependencies required by Scrapy,
# lxml, psycopg2, Crawl4AI and Playwright
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    g++ \
    curl \
    wget \
    git \
    libpq-dev \
    libxml2-dev \
    libxslt1-dev \
    libffi-dev \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for Docker layer caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --upgrade pip setuptools wheel && \
    pip install -r requirements.txt

# Install Chromium and Playwright dependencies
RUN playwright install --with-deps chromium

# Copy application
COPY . .

COPY scrapyd.conf /etc/scrapyd/scrapyd.conf

# Scrapyd runtime directories
RUN mkdir -p \
    /var/lib/scrapyd/logs \
    /var/lib/scrapyd/items \
    /var/lib/scrapyd/jobs \
    /var/lib/scrapyd/dbs \
    /var/lib/scrapyd/eggs

EXPOSE 6800

CMD ["scrapyd", "--pidfile="]