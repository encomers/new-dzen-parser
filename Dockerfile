# ---- Этап сборки зависимостей ----
FROM python:3.14-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --frozen && \
    .venv/bin/python -c "import openai; print('✅ openai installed')"

# ---- Финальный образ ----
FROM python:3.14-slim

# Временная зона
ENV TZ=Europe/Moscow
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# Системные зависимости: Chromium и драйвер
RUN apt-get update && apt-get install -y \
    chromium \
    chromium-driver \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Создаём пользователя с домашней директорией
RUN addgroup --system --gid 1001 appuser && \
    adduser --system --uid 1001 --gid 1001 --home /home/appuser appuser

WORKDIR /app

# Копируем виртуальное окружение
COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

# Копируем исходный код
COPY . .

# Назначаем владельца и переключаемся на appuser
RUN chown -R appuser:appuser /app /home/appuser
USER appuser

# Переменные для корректной работы Chrome
ENV HOME=/home/appuser
ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["python", "dzen.py"]
