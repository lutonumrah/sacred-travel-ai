FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DJANGO_SETTINGS_MODULE=config.settings

WORKDIR /app

# curl backs the compose healthcheck; everything else installs from wheels.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Created before the volumes mount so the named volumes inherit this ownership.
RUN useradd --create-home --uid 1000 app \
 && mkdir -p /app/data /app/media /app/staticfiles \
 && chown -R app:app /app

COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

USER app
EXPOSE 8000

ENTRYPOINT ["entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "2", \
     "--threads", "4", \
     "--worker-class", "gthread", \
     "--timeout", "60", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
