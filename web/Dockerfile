# Node 24 LTS (Node 20 est en fin de vie). Image épinglée par digest : mise à jour volontaire
# (`docker buildx imagetools inspect node:24-alpine` puis remplacer le digest, rebuild + tests).
ARG NODE_IMAGE=node:24-alpine@sha256:ebfe2f90462722a7a4de65e91990e97fe0d401c70e0e762c5b53302f905ec1c1

FROM ${NODE_IMAGE} AS deps
WORKDIR /app
COPY package.json package-lock.json ./
# Installation reproductible strictement conforme au lockfile (échoue s'il est désynchronisé).
RUN npm ci --no-audit --no-fund

FROM ${NODE_IMAGE} AS builder
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM ${NODE_IMAGE} AS runner
WORKDIR /app
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
# IMPORTANT : Docker fixe HOSTNAME au nom du conteneur ; sans ça le serveur
# Next standalone s'y lie et n'écoute pas sur 0.0.0.0 -> injoignable.
ENV HOSTNAME=0.0.0.0
ENV PORT=3000
# Fichiers en lecture seule pour l'utilisateur d'exécution (root:root), processus NON root (utilisateur `node` de l'image, UID 1000).
COPY --from=builder /app/public ./public
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/healthcheck.js ./healthcheck.js
USER node
EXPOSE 3000
CMD ["node", "server.js"]
