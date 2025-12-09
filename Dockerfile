FROM python:3.11-slim

WORKDIR /app

# Install restic for backup repository queries
RUN apt-get update && \
    apt-get install -y --no-install-recommends restic && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .

CMD ["python", "server.py"]
