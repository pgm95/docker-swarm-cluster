# syntax=docker/dockerfile:1

# Build Stage
FROM golang:1.25-alpine AS builder

WORKDIR /app

# Install C compiler tools needed for CGO
RUN apk add --no-cache build-base

# Copy and download dependencies first to leverage Docker layer caching
COPY go.mod go.sum ./
RUN go mod download

# Copy the rest of the application source code
COPY . .

# Sync go.mod/go.sum (upstream ships an incomplete go.mod)
RUN go mod tidy

# Build the static binary

RUN CGO_ENABLED=1 GOOS=linux go build -ldflags "-linkmode external -extldflags -static" -o /dvd .

# Final Stage
FROM alpine:latest

COPY --from=builder /dvd /dvd

ENTRYPOINT ["/dvd"]
