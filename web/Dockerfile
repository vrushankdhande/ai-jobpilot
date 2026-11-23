# syntax=docker/dockerfile:1.7
# AI JobPilot web: the built React app + a tiny Node server that serves it and proxies /api to the backend.
# No secret is copied or baked in: ADMIN_TOKEN and BACKEND_BASE_URL are environment variables at RUN time (docker-compose.yml).

# ---------------------------------------------------------------- build the React app
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY tsconfig.json vite.config.ts index.html ./
COPY src ./src
RUN npm run build

# ---------------------------------------------------------------- runtime: production dependencies only
FROM node:22-alpine AS runtime
ENV NODE_ENV=production WEB_HOST=0.0.0.0 WEB_PORT=8501
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund && npm cache clean --force
COPY server ./server
COPY --from=build /app/dist ./dist
# the image's built-in unprivileged user (uid 1000); nothing is written at run time
USER node
EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD node -e "fetch('http://127.0.0.1:8501/web/config').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"
CMD ["node", "server/server.mjs"]
