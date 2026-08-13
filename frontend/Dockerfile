# syntax=docker/dockerfile:1

# --- build: compile the SPA from the committed lock ----------------------
FROM node:26-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

# --- runtime -------------------------------------------------------------
FROM nginx:alpine

ARG VERSION=0.1.0
ARG REVISION=unknown
ARG BUILD_DATE

LABEL org.opencontainers.image.title="healing-hertz frontend" \
      org.opencontainers.image.description="UniFi network diagnostics dashboard (React + Vite, Catppuccin theme)" \
      org.opencontainers.image.authors="Anubhab Behera" \
      org.opencontainers.image.vendor="Anubhab Behera" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.source="https://github.com/anubhabbehera/healing-hertz" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}" \
      org.opencontainers.image.created="${BUILD_DATE}"

COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

# nginx:alpine ships an unprivileged variant of its config; run as nginx user
# by moving the pid/cache dirs it needs to write into.
RUN sed -i 's|listen  *80;|listen 8080;|' /etc/nginx/conf.d/default.conf \
    && sed -i '/^pid /d' /etc/nginx/nginx.conf \
    && sed -i '1i pid /tmp/nginx.pid;' /etc/nginx/nginx.conf \
    && chown -R nginx:nginx /usr/share/nginx/html /var/cache/nginx

USER nginx
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD wget -qO- http://127.0.0.1:8080/ >/dev/null || exit 1
