# Job Application Pipeline — container for cloud deploys (Render, Railway, Fly, HF Spaces).
# The one-page fit uses a pure-Python estimator, so no Word/LibreOffice is needed here.
FROM python:3.12-slim

WORKDIR /app

# Core deps first, for better layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code. Secrets + personal data are excluded via .dockerignore; the demo
# persona (data/*.example.md) IS included so a bare deploy boots populated.
COPY . .

# Bind the platform-provided port (Render/Railway/Fly set $PORT); 8000 by default.
ENV PORT=8000
EXPOSE 8000

CMD ["sh", "-c", "uvicorn api:app --host 0.0.0.0 --port ${PORT}"]
