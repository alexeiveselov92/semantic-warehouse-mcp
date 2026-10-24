# The documentation site — a static build served by nginx.
# Built from the repository's root (the site imports docs/ and CHANGELOG.md):
#   docker build -f website/Dockerfile -t semantic-warehouse-mcp-web .

# ---- build ----
FROM node:22-alpine AS build
WORKDIR /app/website
COPY website/package.json website/package-lock.json ./
RUN npm ci
WORKDIR /app
# The guide's source, kept in one place and imported by scripts/sync-docs.mjs
COPY docs ./docs
COPY CHANGELOG.md ./CHANGELOG.md
COPY website/astro.config.mjs website/tsconfig.json ./website/
COPY website/scripts ./website/scripts
COPY website/src ./website/src
COPY website/public ./website/public
WORKDIR /app/website
RUN npm run build

# ---- serve ----
FROM nginx:alpine
COPY website/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/website/dist /usr/share/nginx/html
EXPOSE 80
# 127.0.0.1, not localhost: nginx listens on IPv4 only, and localhost may resolve to ::1 — a false "unhealthy".
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD wget -qO- http://127.0.0.1/ >/dev/null 2>&1 || exit 1
