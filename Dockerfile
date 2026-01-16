FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies first (better caching)
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy application code
COPY main.py /app/main.py

# Create the default data directories inside the container.
# In production you should mount a volume/bind mount to /app/data
RUN mkdir -p /app/data/nmea_logs /app/data/csv_logs

EXPOSE 2000/tcp

# Uses /app/config.json (mount it via volume in Docker/Docker Compose)
CMD ["python3", "/app/main.py", "-c", "/app/config.json"]
