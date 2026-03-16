# Workspace — Nova AI Chat + Browser Agent

## Overview

pnpm workspace monorepo using TypeScript. Each package manages its own dependencies.

## Stack

- **Monorepo tool**: pnpm workspaces
- **Node.js version**: 20
- **Package manager**: pnpm
- **TypeScript version**: 5.9
- **Frontend**: React + Vite (port 23842 in dev)
- **API framework**: Express 5 (port 8080)
- **Browser Agent**: Python FastAPI (port 8000)
- **Validation**: Zod, drizzle-zod
- **API codegen**: Orval (from OpenAPI spec)
- **Build**: esbuild (CJS bundle)

## Running Services

| Service | Port | Workflow |
|---------|------|----------|
| Nova Chat (React/Vite) | 23842 | `artifacts/nova-chat: web` |
| API Server (Express) | 8080 | `artifacts/api-server: API Server` |
| Browser Agent (Python FastAPI) | 8000 | `Browser Agent Server` |

## Structure

```text
workspace/
├── artifacts/
│   ├── nova-chat/          # React + Vite frontend (Nova AI Chat UI)
│   ├── api-server/         # Express 5 API server (proxies Nova API + Browser Agent)
│   ├── browser-agent-server/ # Python FastAPI browser automation server
│   └── mockup-sandbox/     # Vite component preview server (design tool)
├── lib/                    # Shared libraries
│   ├── api-spec/           # OpenAPI spec + Orval codegen config
│   ├── api-client-react/   # Generated React Query hooks
│   ├── api-zod/            # Generated Zod schemas from OpenAPI
│   └── db/                 # Drizzle ORM schema + DB connection
├── scripts/                # Utility scripts
├── pnpm-workspace.yaml
├── tsconfig.base.json
├── tsconfig.json
└── package.json
```

## Environment Variables / Secrets

- `NOVA_API_KEY` — required for chat completions and browser agent (Nova Amazon API)

## Architecture

- The **Nova Chat** frontend (Vite) proxies `/api` requests to the **API Server** on port 8080
- The **API Server** handles:
  - `POST /api/chat` — proxies to Nova API for AI completions
  - `GET/POST /api/browser/*` — proxies to the Browser Agent Server on port 8000
- The **Browser Agent Server** (Python) controls a real Chromium browser via a CLI tool, streaming results via SSE

## TypeScript & Composite Projects

Every package extends `tsconfig.base.json` which sets `composite: true`. The root `tsconfig.json` lists all packages as project references.

- **Always typecheck from the root** — run `pnpm run typecheck`
- **`emitDeclarationOnly`** — only `.d.ts` files during typecheck; bundling by esbuild/tsx/vite
- **Project references** — each package's `tsconfig.json` must list its deps in `references`

## Root Scripts

- `pnpm run build` — runs `typecheck` first, then recursively runs `build` in all packages
- `pnpm run typecheck` — runs `tsc --build --emitDeclarationOnly` using project references

## Packages

### `artifacts/nova-chat` (`@workspace/nova-chat`)

React 19 + Vite frontend. Features a chat interface and a browser agent control panel.

- Entry: `src/main.tsx`
- Router: `wouter` with `base` set to `BASE_PATH` env var
- Pages: `ChatPage.tsx`, `BrowserAgentPage.tsx`
- Dev server: `PORT=23842 BASE_PATH=/ pnpm --filter @workspace/nova-chat run dev`

### `artifacts/api-server` (`@workspace/api-server`)

Express 5 API server. Routes live in `src/routes/`.

- Entry: `src/index.ts`
- App setup: `src/app.ts`
- Routes: `health.ts`, `chat.ts`, `browser.ts`
- Dev: `PORT=8080 pnpm --filter @workspace/api-server run dev`
- Build: `pnpm --filter @workspace/api-server run build` → `dist/index.cjs`

### `artifacts/browser-agent-server` (`Python`)

FastAPI server that runs browser automation tasks using an LLM + Chromium.

- Entry: `main.py`
- Dev: `PORT=8000 uv run python3 artifacts/browser-agent-server/main.py`

### `lib/db` (`@workspace/db`)

Database layer using Drizzle ORM with PostgreSQL.

- `drizzle.config.ts` — requires `DATABASE_URL`
- Dev migrations: `pnpm --filter @workspace/db run push`

### `lib/api-spec` / `lib/api-zod` / `lib/api-client-react`

OpenAPI spec, generated Zod schemas, and generated React Query hooks.

- Run codegen: `pnpm --filter @workspace/api-spec run codegen`

### `scripts` (`@workspace/scripts`)

Utility scripts package. Run via `pnpm --filter @workspace/scripts run <script>`.
