# 06 - Docker

## Build pipeline by service

### Frontend
```mermaid
flowchart TD
  U[docker compose up] --> D1[Read frontend Dockerfile]
  D1 --> C1[COPY package files]
  C1 --> I1[npm ci]
  I1 --> C2[COPY source]
  C2 --> B1[npm run build]
  B1 --> IMG1[image built]
  IMG1 --> RUN1[container on 3000]
```

### Agent
```mermaid
flowchart TD
  U[docker compose up] --> P[pull agent image]
  P --> R[container create]
  R --> E[inject env and mount ~/.aws]
  E --> A[python app.py on 8000]
```

### YOLO
```mermaid
flowchart TD
  U[docker compose up] --> P[pull yolo image]
  P --> R[container create]
  R --> E[inject env and mount ~/.aws]
  E --> A[python app.py on 8080]
```

## Docker concepts in this repo
- Image: packaged runtime artifact.
- Container: running service instance.
- Build arg: NEXT_PUBLIC_AGENT_URL for frontend build.
- Env vars: runtime wiring for agent/yolo.
- Volume mounts: ~/.aws credentials and named storage.
- Networks: polyai-net and monitoring-net.
