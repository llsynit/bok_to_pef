FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MODULE_NAME=insert_metadata \
    PORT=9013 \
    APP_HOME=/app

WORKDIR ${APP_HOME}

# Installer Python-avhengigheter
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --upgrade pip setuptools wheel \
 && pip install --no-cache-dir -r requirements.txt

# Kopier app-koden
COPY insert_metadata.py ./insert_metadata.py
COPY app.py ./app.py

# Ikke-root
RUN useradd -ms /bin/bash appuser
USER appuser

EXPOSE 9013
HEALTHCHECK --interval=20s --timeout=3s --retries=5 CMD python -c "import socket; s=socket.create_connection(('127.0.0.1', 9013), 2); s.close()"

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "9013"]
