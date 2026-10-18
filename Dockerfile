ARG PYTHON_IMAGE=python:3.12-slim
FROM ${PYTHON_IMAGE}

WORKDIR /app
COPY monitor.py targets.json ./
COPY docs ./docs
COPY examples ./examples
COPY scripts ./scripts

EXPOSE 8080
ENTRYPOINT ["python", "monitor.py"]
CMD ["--help"]
