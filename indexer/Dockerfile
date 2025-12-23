FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y postgresql-client && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY ./indexer/requirements.txt ./indexer/requirements.txt
RUN pip install --no-cache-dir -r ./indexer/requirements.txt
RUN mkdir -p /app
COPY . /app
COPY indexer/entrypoint.sh /app/indexer/entrypoint.sh
COPY indexer/wait_for_schema.sh /app/indexer/wait_for_schema.sh
RUN chmod +x /app/indexer/entrypoint.sh /app/indexer/wait_for_schema.sh
ENTRYPOINT ["/app/indexer/wait_for_schema.sh"]
CMD ["/bin/sh", "/app/indexer/entrypoint.sh"]
