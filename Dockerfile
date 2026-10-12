FROM python:3.11-slim

WORKDIR /app

# System deps needed to build psycopg2 and bcrypt
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/media

EXPOSE 8000

# CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
CMD alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${WEB_CONCURRENCY:-2} --proxy-headers --forwarded-allow-ips="*"
