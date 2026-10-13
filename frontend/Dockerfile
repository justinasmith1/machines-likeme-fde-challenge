# Frontend. Two stages: the first installs every dependency and builds; the second is the
# image that runs, with only Next.js's standalone output (the server and the production
# dependencies it traced). No dev dependencies, no tests, no .env: see .dockerignore, and
# infra/03-build-push.sh checks the result before pushing.

FROM node:20-alpine AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# NEXT_PUBLIC_* values are fixed into the bundle at build time, so the identity provider
# is chosen here (lib/auth/index.ts). This image is for Azure: Microsoft Entra ID.
ARG NEXT_PUBLIC_AUTH_MODE=entra
ENV NEXT_PUBLIC_AUTH_MODE=$NEXT_PUBLIC_AUTH_MODE
RUN npx next build


FROM node:20-alpine
WORKDIR /app
# The proxy reads NEXT_PUBLIC_AUTH_MODE from the running server's environment; it has to
# agree with the value the bundle was built with. API_BASE_URL, API_PROXY_TARGET and
# MAX_UPLOAD_MB are set by the deployment.
ARG NEXT_PUBLIC_AUTH_MODE=entra
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    NEXT_PUBLIC_AUTH_MODE=$NEXT_PUBLIC_AUTH_MODE \
    HOSTNAME=0.0.0.0 \
    PORT=3000
COPY --from=build --chown=node:node /app/.next/standalone ./
COPY --from=build --chown=node:node /app/.next/static ./.next/static
USER node
EXPOSE 3000
CMD ["node", "server.js"]
