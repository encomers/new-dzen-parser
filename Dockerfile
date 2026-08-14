# ---- Этап сборки зависимостей ----
FROM python:3.14-slim AS builder

# Устанавливаем uv (последняя версия)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Копируем только файлы с зависимостями
COPY pyproject.toml uv.lock ./

# Создаём venv в /opt/venv и устанавливаем пакеты через uv sync
# Указываем venv через переменную VIRTUAL_ENV
RUN uv venv /opt/venv && \
    VIRTUAL_ENV=/opt/venv uv sync --no-dev --frozen && \
    # Проверяем, что openai действительно установлен
    /opt/venv/bin/python -c "import openai; print('✅ openai installed')" || \
    (echo "❌ openai not found" && exit 1)

# ---- Финальный образ ----
FROM python:3.14-slim

# Временная зона
ENV TZ=Europe/Moscow
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# Системные зависимости для Selenium (Chromium + драйвер)
RUN apt-get update && apt-get install -y \
    chromium \
    chromium-driver \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Создаём непривилегированного пользователя
RUN addgroup --system --gid 1001 appuser && \
    adduser --system --uid 1001 --gid 1001 appuser

WORKDIR /app

# Копируем виртуальное окружение из builder
COPY --from=builder /opt/venv /opt/venv

# Добавляем venv в PATH (чтобы python и pip были из него)
ENV PATH="/opt/venv/bin:$PATH"

# Копируем исходный код
COPY . .

# Назначаем владельца и переключаемся на непривилегированного пользователя
RUN chown -R appuser:appuser /app
USER appuser

# Отключаем буферизацию вывода Python
ENV PYTHONUNBUFFERED=1

# Точка входа
ENTRYPOINT ["python", "dzen.py"]
