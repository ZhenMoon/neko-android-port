FROM node:20-alpine AS builder

WORKDIR /app
COPY package.json tsconfig.json ./
RUN npm install
COPY src/ src/
RUN npx tsc

FROM node:20-alpine AS runner

WORKDIR /app

RUN apk add --no-cache tini

COPY --from=builder /app/build/ build/
COPY --from=builder /app/node_modules/ node_modules/
COPY package.json tsconfig.json ./

ENV NODE_ENV=production

ENTRYPOINT ["tini", "--"]
CMD ["node", "build/index.js"]
