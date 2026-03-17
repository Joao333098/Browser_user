FROM node:20-slim
WORKDIR /app

RUN npm install -g pnpm

COPY package.json pnpm-workspace.yaml pnpm-lock.yaml .npmrc ./
COPY lib/ lib/
COPY artifacts/nova-chat/ artifacts/nova-chat/
COPY artifacts/api-server/ artifacts/api-server/
COPY artifacts/mockup-sandbox/package.json artifacts/mockup-sandbox/
COPY tsconfig.base.json tsconfig.json ./

RUN pnpm install --frozen-lockfile

ENV PORT=8080
ENV BASE_PATH=/
RUN pnpm --filter @workspace/nova-chat build

RUN pnpm --filter @workspace/api-server build

RUN mkdir -p artifacts/api-server/dist/public
RUN cp -r artifacts/nova-chat/dist/public/. artifacts/api-server/dist/public/

EXPOSE 8080
ENV NODE_ENV=production

CMD ["node", "artifacts/api-server/dist/index.cjs"]
