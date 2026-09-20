FROM python:3.11-slim

# Кодирование изображений и хеширование считаются по-настоящему, поэтому
# нужны системные библиотеки, а не только пакеты Python.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl libjpeg62-turbo zlib1g \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY docker/requirements.txt /app/docker/requirements.txt
RUN pip install --no-cache-dir -r /app/docker/requirements.txt

COPY core /app/core
COPY workers /app/workers
COPY demo /app/demo
COPY experiments /app/experiments
COPY gateway.py /app/gateway.py
COPY data /app/data

ENV PYTHONUNBUFFERED=1 PYTHONPATH=/app BIND_HOST=0.0.0.0
