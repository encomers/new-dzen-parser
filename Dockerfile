# ---- Этап сборки зависимостей ----
FROM python:3.14-slim AS builder

# Устанавливаем uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Копируем только файлы с зависимостями (для кэширования)
COPY pyproject.toml uv.lock ./

# Создаём виртуальное окружение и устанавливаем зависимости
RUN uv venv /opt/venv && \
    . /opt/venv/bin/activate && \
    uv sync --no-dev --frozen && \
    # Проверяем, что openai установлен
    /opt/venv/bin/python -c "import openai; print('✅ openai installed')" || \
    (echo "❌ openai installation failed" && exit 1)

# ---- Финальный образ ----
FROM python:3.14-slim

# Установка временной зоны
ENV TZ=Europe/Moscow
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# Системные зависимости для Selenium (Chromium и драйвер)
RUN apt-get update && apt-get install -y \
    chromium \
    chromium-driver \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Создаём непривилегированного пользователя
RUN addgroup --system --gid 1001 appuser && \
    adduser --system --uid 1001 --gid 1001 appuser

WORKDIR /app

# Копируем виртуальное окружение из builder'а
COPY --from=builder /opt/venv /opt/venv

# Добавляем путь к исполняемым файлам venv в PATH
ENV PATH="/opt/venv/bin:$PATH"

# Копируем исходный код проекта
COPY . .

# Назначаем владельца и переключаемся на непривилегированного пользователя
RUN chown -R appuser:appuser /app
USER appuser

# Переменная для немедленного вывода логов
ENV PYTHONUNBUFFERED=1

# Точка входа – запуск парсера
ENTRYPOINT ["python", "dzen.py"]
