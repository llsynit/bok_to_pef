FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MODULE_NAME=bok_to_pef \
    PORT=7013 \
    APP_HOME=/app

WORKDIR ${APP_HOME}

# --- Java (for Saxon) ---
# Use OpenJDK 21 on Debian trixie
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
    openjdk-21-jre-headless \
    ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

# (optional) expose JAVA_HOME; path is correct on Debian for JDK/JRE 21
ENV JAVA_HOME=/usr/lib/jvm/java-21-openjdk-arm64
# If you also build amd64, JAVA_HOME is /usr/lib/jvm/java-21-openjdk-amd64 there.
# You can skip JAVA_HOME entirely if your code only shells `java`.
ENV PATH="${JAVA_HOME}/bin:${PATH}"

# Installer Python-avhengigheter
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --upgrade pip setuptools wheel \
    && pip install --no-cache-dir -r requirements.txt

# create non-root and make /app writable
RUN useradd -u 1000 -ms /bin/bash appuser \
    && chown -R appuser:appuser /app

# copy code ONCE with correct ownership (remove the earlier root COPY)
COPY --chown=appuser:appuser . /app

USER appuser

EXPOSE 7013
HEALTHCHECK --interval=20s --timeout=3s --retries=5 CMD python -c "import socket; s=socket.create_connection(('127.0.0.1', 7013), 2); s.close()"

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "39013"]
