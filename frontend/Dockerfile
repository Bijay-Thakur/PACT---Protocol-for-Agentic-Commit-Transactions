# syntax=docker/dockerfile:1
# PACT operator console (Next.js standalone build)

FROM node:22-alpine AS deps
WORKDIR /app
RUN apk add --no-cache libc6-compat
COPY package.json package-lock.json ./
RUN npm ci

FROM node:22-alpine AS builder
WORKDIR /app
ARG NEXT_PUBLIC_PACT_API_URL=http://localhost:8000
ENV NEXT_PUBLIC_PACT_API_URL=$NEXT_PUBLIC_PACT_API_URL
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:22-alpine AS runner
WORKDIR /app
ARG NEXT_PUBLIC_PACT_API_URL=http://localhost:8000
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    NEXT_PUBLIC_PACT_API_URL=$NEXT_PUBLIC_PACT_API_URL \
    PORT=3000 \
    HOSTNAME=0.0.0.0
RUN addgroup -S nodejs -g 1001 && adduser -S nextjs -u 1001 -G nodejs
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static
COPY --from=builder --chown=nextjs:nodejs /app/public ./public
USER nextjs
EXPOSE 3000
CMD ["node", "server.js"]
