# 08 - GitHub Actions

## Deploy visual flow
```mermaid
flowchart TD
  P[Push to dev/main] --> W[Deploy workflow]
  W --> R[Runner starts]
  R --> S[SSH to EC2]
  S --> F[git fetch]
  F --> C[git checkout branch]
  C --> H[git reset --hard origin branch]
  H --> D1[docker compose pull]
  D1 --> D2[docker compose down]
  D2 --> D3[docker compose build frontend]
  D3 --> D4[docker compose up]
  D4 --> HC[curl health checks]
  HC --> X[Finished]
```

## Why each step
- fetch/checkout/reset: deterministic server code state.
- pull/down/build/up: fresh runtime and rebuilt frontend config.
- health checks: ensure real readiness before success.

## Test workflow visual
```mermaid
flowchart LR
  PR[Pull request to main] --> TY[YOLO tests]
  PR --> TA[Agent tests]
  PR --> DS[Docker scout scans]
```
