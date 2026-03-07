# Build stage: concatenate modules into single file
FROM python:3.13.8-alpine AS builder
WORKDIR /build
COPY build.py .
COPY forwarder/ forwarder/
RUN python build.py

# Runtime stage: minimal image with generated script
FROM python:3.13.8-alpine
WORKDIR /app
COPY --from=builder /build/forwarder.py .
RUN adduser -D forwarder
USER forwarder
CMD ["python", "-u", "forwarder.py"]
