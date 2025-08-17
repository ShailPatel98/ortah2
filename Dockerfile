# -------- base --------
FROM python:3.11-slim

WORKDIR /app

# install python deps
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# app code
COPY server ./server
COPY web ./web
COPY scraper ./scraper
COPY indexer ./indexer

# make sure data dir exists even if it's not in git
RUN mkdir -p /app/data

EXPOSE 8000

CMD ["uvicorn", "server.app:app", "--host", "0.0.0.0", "--port", "8000"]
