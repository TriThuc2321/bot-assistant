FROM python:3.12-slim
# TIKTOKEN_CACHE_DIR: bake the o200k_base encoding into the image so each run
# doesn't re-download it (chunk estimates in spec 03 need it).
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TIKTOKEN_CACHE_DIR=/app/.tiktoken
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && python -c "import tiktoken; tiktoken.get_encoding('o200k_base')"
COPY . .
RUN useradd -m app && chown -R app /app
USER app
CMD ["python", "main.py"]
