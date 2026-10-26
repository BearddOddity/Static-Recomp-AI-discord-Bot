FROM python:3.12-slim

# git is needed to read GitHub wikis (DOCS_REPOS entries ending in .wiki)
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY bot ./bot

# Cases, warnings and the saved rules live here; mount it so they survive updates.
VOLUME /app/data
CMD ["python", "-m", "bot.main"]
