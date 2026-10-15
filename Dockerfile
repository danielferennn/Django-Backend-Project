FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MEDIA_ROOT=/app/media \
    PRIVATE_MEDIA_ROOT=/app/private_media

WORKDIR /app

RUN apt-get update -o Acquire::ForceIPv4=true \
    && apt-get install -o Acquire::ForceIPv4=true -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        libsm6 \
        libxext6 \
        libxrender1 \
        libgomp1 \
    && groupadd --system --gid 10001 smartlocker \
    && useradd --system --uid 10001 --gid smartlocker --home-dir /home/smartlocker --create-home --shell /usr/sbin/nologin smartlocker \
    && mkdir -p /app/media /app/private_media \
    && chown -R smartlocker:smartlocker /app/media /app/private_media \
    && chmod 0750 /app/private_media \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY --chown=0:0 . /app

USER smartlocker
CMD ["gunicorn", "smartlocker.wsgi:application", "--bind", "0.0.0.0:8000"]
