FROM python:3.11-slim
LABEL org.opencontainers.image.source="https://github.com/didacnashe-cmd/beira" \
      org.opencontainers.image.title="Beira" \
      org.opencontainers.image.description="Proxy MCP que re-expone un MCP local a contactos, con permisos."
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    BEIRA_DB=/data/beira.db
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py .
COPY beira beira
# /data pertenece al usuario sin privilegios: el volumen hereda ese dueño.
RUN mkdir /data && chown 1000:1000 /data
USER 1000:1000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import os,urllib.request as u; u.urlopen('http://127.0.0.1:%s/healthz' % os.getenv('BEIRA_PORT','8100'), timeout=3)"
CMD ["python", "server.py"]
