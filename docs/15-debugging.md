# 15 - Debugging Runbook

## Quick health checklist
1. Are containers running?
2. Are logs clean?
3. Do health endpoints respond?
4. Can frontend call agent?
5. Can agent call yolo?
6. Can yolo access S3 and DB?

## Commands

### Container state
```bash
docker compose ps
docker compose images
```

### Logs
```bash
docker compose logs -f
docker compose logs -f frontend
docker compose logs -f agent
docker compose logs -f yolo
docker compose logs -f prometheus
docker compose logs -f grafana
```

### Execute inside containers
```bash
docker compose exec agent sh
docker compose exec yolo sh
docker compose exec frontend sh
```

### Network and volume checks
```bash
docker network ls
docker network inspect polyaifursa-private_polyai-net
docker network inspect polyaifursa-private_monitoring-net
docker volume ls
```

### Endpoint checks
```bash
curl http://localhost:3000
curl http://localhost:8000/health
curl http://localhost:8080/health
curl http://localhost:8080/ready
curl http://localhost:8080/metrics
curl http://localhost:9090/-/healthy
curl http://localhost:3001
```

## Common problems and fixes

### Frontend shows errors when sending chat
- likely cause: wrong NEXT_PUBLIC_AGENT_URL at build time.
- fix: rebuild frontend image with correct build arg.

### Agent tool call fails
- likely cause: wrong YOLO_SERVICE_URL.
- fix: inside compose, set to http://yolo:8080.

### S3 errors in yolo or agent logs
- likely cause: missing/invalid ~/.aws credentials, wrong bucket/region.
- fix: verify mounted ~/.aws and IAM permissions.

### Health checks fail after deployment
- likely cause: one service failed startup.
- fix: inspect remote docker compose logs and env values.

### Prometheus target DOWN
- likely cause: yolo not reachable on polyai-net or /metrics unavailable.
- fix: inspect yolo logs and test curl from prometheus-connected context.
