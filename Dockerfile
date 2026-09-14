FROM python:3.11-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md .
RUN pip install --no-cache-dir .

COPY . .

VOLUME /app/config
VOLUME /app/data

EXPOSE 5005/tcp

ENV FLASK_APP=app.py

CMD ["./run.sh"]
