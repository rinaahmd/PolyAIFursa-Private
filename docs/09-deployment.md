# 09 - Deployment

## Timeline after git push origin main
```mermaid
flowchart TD
  A[Developer push] --> B[GitHub trigger]
  B --> C[deploy-prod starts]
  C --> D[SSH to EC2]
  D --> E[repo sync commands]
  E --> F[docker compose refresh]
  F --> G[health checks]
  G --> H[success or failure]
```

## Command purpose summary
- git fetch: update remote references.
- git checkout: select branch.
- git reset --hard: exact branch state.
- compose pull: update images.
- compose build frontend: bake URL config.
- compose up: run target services.
