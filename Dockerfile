FROM python:3.12-slim

WORKDIR /app

# Without this, Railway's logs look dead — Python buffers stdout in containers.
ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "main.py"]
