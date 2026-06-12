FROM python:3.11-slim

# Install system dependencies for Peewee/PostgreSQL adapter compiling fallback
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project source files
COPY . .

# Expose health check server port (Render binds to this)
EXPOSE 8080

# Run the trading bot in 'both' mode
CMD ["python", "main.py", "--mode", "both", "trade"]
