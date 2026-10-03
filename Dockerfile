FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app

WORKDIR /app

# System dependencies
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

# Install Python dependencies
COPY requirements.txt .

RUN pip install --upgrade pip setuptools wheel && \
    pip install -r requirements.txt

# Install Chromium for Playwright
RUN playwright install --with-deps chromium

# Copy application
COPY . .

# Scrapyd configuration
COPY scrapyd.conf /etc/scrapyd/scrapyd.conf

# Scrapyd directories
RUN mkdir -p \
    /var/lib/scrapyd/logs \
    /var/lib/scrapyd/items \
    /var/lib/scrapyd/jobs \
    /var/lib/scrapyd/dbs \
    /var/lib/scrapyd/eggs

# Make entrypoint executable
RUN chmod +x /app/entrypoint.sh

# Verify Celery and Redis are installed in the image
RUN celery --version && \
    python -c "import redis; print('redis:', redis.__version__)"

EXPOSE 6800

ENTRYPOINT ["/app/entrypoint.sh"]