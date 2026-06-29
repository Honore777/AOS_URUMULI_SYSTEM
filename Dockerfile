FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive

# Install tiny runtime deps and CBC solver
RUN apt-get update && apt-get install -y --no-install-recommends \
    coinor-cbc \
    curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . /app

# Ensure gunicorn is available; install requirements if provided
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir gunicorn && \
    if [ -f requirements.txt ]; then pip install --no-cache-dir -r requirements.txt; fi

EXPOSE 10000

# Render sets $PORT; use shell form so $PORT is expanded
CMD ["bash", "-lc", "gunicorn app:app -b 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 120"]
