#!/bin/sh

set -e

echo "======================================"
echo "Starting Scrapyd"
echo "======================================"

scrapyd --pidfile=/tmp/scrapyd.pid &

SCRAPYD_PID=$!

echo "Waiting for Scrapyd to become ready..."

until curl -sf http://127.0.0.1:6800/ >/dev/null 2>&1; do
    sleep 2
done

echo "Scrapyd is ready."

echo "======================================"
echo "Deploying Scrapy project"
echo "======================================"

scrapyd-deploy local

echo "======================================"
echo "Scrapy project deployed successfully"
echo "======================================"

wait $SCRAPYD_PID