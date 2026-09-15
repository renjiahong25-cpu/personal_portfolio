FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY requirements-quote.txt ./
RUN pip install --no-cache-dir -r requirements-quote.txt
COPY config ./config
COPY db ./db
COPY api ./api
COPY core ./core
COPY schemas ./schemas
COPY service ./service
COPY data ./data
COPY deploy ./deploy
COPY main.py ./
COPY frontend/dist ./frontend/dist
ENV QUOTE_ONLY=1
EXPOSE 8080
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]