FROM python:3.14-slim@sha256:0514bc76712d64f8bb48adcb95b1ab0d88c69fee95d62fe4c18ee948bee9b1e1
RUN pip install --no-cache-dir cryptography==50.0.2 PyYAML==6.0.3
WORKDIR /app
COPY runtime/ /app/
CMD ["python", "-B", "deployment/bootstrap/haos_addon/app.py"]
