# ---- Этап сборки зависимостей ----
FROM python:3.14-slim AS builder

# Устанавливаем uv (быстрый менеджер пакетов)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Копируем только файлы зависимостей для кэширования слоёв
COPY pyproject.toml uv.lock ./

# Создаём виртуальное окружение и устанавливаем зависимости (без dev)
RUN uv venv /opt/venv && \
    . /opt/venv/bin/activate && \
    uv sync --no-dev --frozen

# ---- Финальный образ ----
FROM python:3.14-slim

# Устанавливаем временную зону (для планировщика)
ENV TZ=Europe/Moscow
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# Системные зависимости: Chromium и драйвер для Selenium
RUN apt-get update && apt-get install -y \
    chromium \
    chromium-driver \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Создаём непривилегированного пользователя (безопасность)
RUN addgroup --system --gid 1001 appuser && \
    adduser --system --uid 1001 --gid 1001 appuser

WORKDIR /app

# Копируем виртуальное окружение из предыдущего этапа
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Копируем исходный код проекта
COPY . .

# Назначаем владельца для рабочей директории
RUN chown -R appuser:appuser /app

# Переключаемся на непривилегированного пользователя
USER appuser

# Переменные окружения для Python
ENV PYTHONUNBUFFERED=1

# Точка входа – запуск парсера
ENTRYPOINT ["python", "dzen.py"]
