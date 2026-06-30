FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Bake the read-only sample data into the image so the container is
# self-contained on Kubernetes (no data volume to provision).
COPY data ./data

ENV DATA_DIR=/app/data \
    CACHE_DIR=/app/cache

# Precompute the expensive t-SNE/UMAP projections into the image. On Kubernetes
# there is no cache volume (helm pvcs: []), so without this the deployment would
# only serve PCA and return HTTP 425 for t-SNE/UMAP. Building here also guarantees
# the cache always matches the baked-in data (never stale).
RUN python -m app.projections

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
