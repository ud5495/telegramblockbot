FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN useradd --create-home --uid 10001 bot

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY bot ./bot
COPY main.py ./

RUN mkdir -p /app/data && chown -R bot:bot /app

USER bot

CMD ["python", "main.py"]
