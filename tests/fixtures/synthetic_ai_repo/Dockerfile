# =====================================================================
# Acme Gateway — Production-Ready Container Image
# =====================================================================
# WHAT IT DOES:
#   Builds a minimal, battle-tested, production-ready image for the
#   Acme Gateway service. Ships ready-to-use, no client software needed.
# =====================================================================
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8080
CMD ["python", "-m", "app"]
