# Воспроизводимый запуск решения: API мини-приложения, статика и бот в одном
# процессе. Требование хакатона (FAQ, вопрос 7).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Зависимости отдельным слоем: пересобираются только при их изменении
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/
COPY frontend/ ./frontend/
COPY data/demo_1c.json ./data/demo_1c.json
COPY tools/ ./tools/
COPY run.py README.md AGENT.md ./

# Внутри контейнера слушаем все интерфейсы, наружу порт пробрасывает compose
ENV HOST=0.0.0.0 \
    PORT=8099 \
    DB_PATH=/app/data/app.sqlite3

EXPOSE 8099

# Бот и API должны оставаться доступными весь период проверки (FAQ, вопрос 11),
# поэтому healthcheck: оркестратор перезапустит упавший контейнер.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8099/api/health', timeout=4).status == 200 else 1)"

CMD ["python", "run.py"]
