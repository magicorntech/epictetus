FROM golang:1.22-alpine AS builder
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -ldflags="-s -w" -o /epictetus .

FROM alpine:3.19
RUN apk add --no-cache ca-certificates && \
    addgroup -S epictetus && adduser -S epictetus -G epictetus
USER epictetus
COPY --from=builder /epictetus /epictetus
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -qO- http://localhost:8080/health/live || exit 1
ENTRYPOINT ["/epictetus"]
