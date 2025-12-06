FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FASTAPIPORT=8001

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Document the container port; override FASTAPIPORT to change.
EXPOSE 8001

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${FASTAPIPORT:-8001}"]
