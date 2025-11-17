FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    POETRY_VIRTUALENVS_CREATE=false

WORKDIR /app

RUN apt-get update -o Acquire::ForceIPv4=true \
    && apt-get install -o Acquire::ForceIPv4=true -y --no-install-recommends \
        build-essential \
        libpq-dev \
        libgl1 \
        libglib2.0-0 \
        libsm6 \
        libxext6 \
        libxrender1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY . /app

CMD ["gunicorn", "smartlocker.wsgi:application", "--bind", "0.0.0.0:8000"]
