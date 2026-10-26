FROM python:3.12-slim

# git reads GitHub wikis (DOCS_REPOS entries ending in .wiki) and does self-updates.
# docker-compose mounts the clone, owned by another user, so git must trust it.
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && git config --system --add safe.directory '*'

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# The bot plus any server profiles (servers/); .dockerignore keeps secrets and data out.
COPY . .

# Cases, warnings and the saved rules live here; mount it so they survive updates.
VOLUME /app/data
CMD ["python", "-m", "bot.main"]
