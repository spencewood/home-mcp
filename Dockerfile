FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY test_mikrotik.py .
COPY server.py .

CMD ["python", "server.py"]
