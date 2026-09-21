# Backend-only image per implementation-plan.md Phase 7 (the Streamlit
# frontend in frontend/ is a separate process, not containerized here).
#
# Ingests at BUILD time rather than at container startup: bakes a
# deterministic data/restaurants.parquet into the image so the running
# container never depends on Hugging Face being reachable, at the cost of
# `docker build` itself needing network access. (The plan allows either;
# this picks build-time for a more reliable, reproducible running container.)
#
# Build:  docker build -t restaurant-recommender .
# Run:    docker run -p 8000:8000 -e GROQ_API_KEY=your-key-here restaurant-recommender
FROM python:3.12.10-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/

RUN python -m src.ingestion.ingest

EXPOSE 8000

CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
