FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install dependencies first so this layer is cached when only code changes
COPY pyproject.toml ./
RUN mkdir app && touch app/__init__.py && pip install . && rm -rf app

COPY . .
RUN pip install --no-deps . && useradd --create-home lumen && mkdir -p /data && chown lumen /data
USER lumen

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
