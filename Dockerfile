FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive
ARG HIGHS_VER=1.5.0

# Install tiny runtime deps and HiGHS binary
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget ca-certificates tar coinor-cbc && \
    wget -qO /tmp/highs.tar.gz "https://github.com/ERGO-Research/HiGHS/releases/download/v${HIGHS_VER}/highs_${HIGHS_VER}_Linux_x86_64.tar.gz" && \
    tar -xzf /tmp/highs.tar.gz -C /tmp && \
    cp /tmp/highs*/bin/highs /usr/local/bin/highs && chmod +x /usr/local/bin/highs && \
    rm -rf /var/lib/apt/lists/* /tmp/highs*

WORKDIR /app
COPY . /app

# Ensure gunicorn is available; install requirements if provided
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir gunicorn && \
    if [ -f requirements.txt ]; then pip install --no-cache-dir -r requirements.txt; fi

EXPOSE 10000

# Render sets $PORT; use shell form so $PORT is expanded
CMD ["bash", "-lc", "gunicorn app:app -b 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 120"]
