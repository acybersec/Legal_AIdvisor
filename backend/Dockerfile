FROM python:3.13-slim

WORKDIR /app

# lxml are nevoie de biblioteci de sistem pentru build-ul de wheel
RUN apt-get update && apt-get install -y --no-install-recommends \
      libxml2 libxslt1.1 \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app

# Corpusul si indexul se monteaza ca volum. Nu intra in imagine: se schimba
# la fiecare reingestie si ar umfla build-ul cu zeci de MB.
ENV PYTHONUNBUFFERED=1
EXPOSE 8000

CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000"]
