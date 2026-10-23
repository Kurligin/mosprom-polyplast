FROM python:3.12-slim

ENV PYTHONPATH=/code \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /code

# Шрифты с кириллицей и эмодзи — для картинки сводки
RUN apt-get update && apt-get install -y --no-install-recommends fonts-noto-core fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

# Chromium для Playwright (рендер сводки) — внутри образа, не в томе
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN playwright install --with-deps chromium && rm -rf /var/lib/apt/lists/*

# Кэш моделей (bge-m3) — в именованном томе, скачивается один раз
ENV HF_HOME=/models/hf

# Сертификаты НУЦ Минцифры (нужны для GigaChat) + стандартные из certifi — в один бандл
COPY certs/russian_trusted_ca_bundle.pem /tmp/russian_ca.pem
RUN cat "$(python -c 'import certifi; print(certifi.where())')" /tmp/russian_ca.pem > /etc/ssl/korsik-bundle.pem \
    && rm /tmp/russian_ca.pem
ENV SSL_CERT_FILE=/etc/ssl/korsik-bundle.pem \
    REQUESTS_CA_BUNDLE=/etc/ssl/korsik-bundle.pem

# Код монтируется томом в docker-compose; копия — для запуска без тома
COPY . .

EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
