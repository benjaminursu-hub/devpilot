#!/bin/sh
set -e

case "$1" in
  worker)
    # Background analysis worker (Redis queue consumer).
    exec python -m app.worker
    ;;
  *)
    # API: bring the database schema up to date, then serve.
    alembic upgrade head
    exec uvicorn app.main:app --host 0.0.0.0 --port 8000
    ;;
esac
