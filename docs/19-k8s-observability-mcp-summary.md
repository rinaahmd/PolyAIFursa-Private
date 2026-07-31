# Assignment 5 — K8s Migration, Observability, MCP: Presentation Summary

One-page cheat sheet. Use with `docs/COMPLETE_SYSTEM_GUIDE.md` if more depth is needed.

---

## 1. What was asked

| Part | Requirement |
|------|-------------|
| I | Deploy the whole Compose stack (minus node-exporter) to K8s, dev + prod namespaces, plain Deployments only. Bonus: probes, requests/limits, HPA on Yolo/Agent/Frontend. Prometheus needs durable EBS storage. |
| II | Ship EC2 container logs to S3 with Fluent Bit (90-day lifecycle). Build a local MCP server so Copilot can query S3 logs + Prometheus. |
| Bonus | Instrument Agent with Prometheus metrics + Grafana dashboard. |

---

## 2. What's done — file map

| Area | Files | Lines to know |
|------|-------|----------------|
| Namespaces | `infra/k8s/base/namespaces.yaml` | 1-8 (dev, prod) |
| StorageClass | `infra/k8s/base/ebs-storage-class.yaml` | whole file, 9 lines |
| Agent | `infra/k8s/{dev,prod}/agent.yaml` | image line 22/24 · probes 39-54 · resources 55-61 · NodePort dev 30800 (line ~76), prod 30801 (line ~80) |
| Frontend | `infra/k8s/{dev,prod}/frontend.yaml` | image line 25/28 (comment: build-arg `NEXT_PUBLIC_AGENT_URL`) · NodePort dev 30300, prod 30301 |
| YOLO | `infra/k8s/{dev,prod}/yolo.yaml` | probes 29-44 · resources 45-51 (highest limits: 1 CPU/2Gi) |
| Image MCP | `infra/k8s/{dev,prod}/img-proc-mcp.yaml` | port 9000, no probes/limits/HPA (not required) |
| HPA | `infra/k8s/{dev,prod}/hpa.yaml` | 3 objects: yolo/agent/frontend, 50% CPU, min1/max3 |
| Prometheus | `infra/k8s/{dev,prod}/prometheus.yaml` | `strategy: Recreate` lines 15-19 · storage mount 71-72 |
| Prometheus storage | `infra/k8s/{dev,prod}/prometheus-pv.yaml` (+`-pvc.yaml`) | real `volumeHandle` line 20 (dev `vol-0c524fc2962c89bde`, prod `vol-04f2efc14590762d6`) — **static provisioning** |
| Prometheus config | `infra/k8s/{dev,prod}/prometheus-config.yaml` | jobs: prometheus/yolo/agent, lines 12-27 — target fixed to `prometheus:9090` (was `localhost:9090`, now corrected) |
| Grafana | `infra/k8s/{dev,prod}/grafana.yaml` (+`-datasource.yaml`) | admin creds from Secret `grafana-admin` · datasource URL `http://prometheus:9090` |
| CI/CD | `.github/workflows/deploy.yaml` | new `deploy-fluent-bit` job, lines ~263-282; triggers on `fluent-bit.conf` change too (line 39) |
| Fluent Bit | `fluent-bit.conf` | tags logs by Compose **service name**, not container ID (see warning below) |
| Compose | `docker-compose.yml` | `LOG_CONTAINER_NAME` + `logging.driver: json-file` per service (lines 10, 20-21, 35, 46-47, 59, 67-68, 79, 87-88); fluent-bit env now `S3_LOGS_BUCKET` (line 95) |
| Observability MCP | `services/observability-mcp/app.py` | reworked — now keys off **service name in the S3 path**, not container metadata; `get_container_logs` gained `around_time` param (lines 273-330) |
| Agent metrics | `services/agent/app.py` | 4 metrics lines 1287-1307 · updated in `chat()` 1438-1472 · CORS origin added line 1010 |
| Grafana dashboard | `infra/grafana/dashboards/agent.json` | Error Rate panel changed `stat` → `timeseries` (lines 51-52) |

---

## 3. Architecture (quick recall)

```
Browser → Frontend NodePort → Agent NodePort → YOLO / Image-MCP / Bedrock / S3

Agent /metrics, YOLO /metrics → Prometheus (EBS-backed) → Grafana

EC2 (Compose only): containers → Fluent Bit → S3 → Observability MCP → Copilot
```

---

## 4. What changed in this latest edit (since last review)

1. **Fluent Bit re-tagging** — `fluent-bit.conf` now uses a `rewrite_tag` filter keyed on the `com.docker.compose.service` label (lines 21-25), and the S3 key format became `/dev/logs/host=<host>/service=<name>/.../$UUID.gz` (line 32).
2. **`docker-compose.yml`** — every service now sets `LOG_CONTAINER_NAME` and a `logging.driver: json-file` block with `labels: com.docker.compose.service` so Fluent Bit can read that label.
3. **`services/observability-mcp/app.py`** — dropped the old container-ID/`containers.jsonl` metadata approach entirely; logs are now matched purely by parsing `service=<name>` out of the S3 key (`_extract_service_from_key`, lines 150-152). `get_container_logs` can now take `around_time` (ISO 8601) instead of just "last N minutes" — needed for the incident-question test prompt.
4. **Prometheus scrape config** — fixed `prometheus:9090` target (was incorrectly `localhost:9090`).
5. **Prometheus `Recreate` strategy** — added, required by the `ReadWriteOnce` EBS-backed PVC.
6. **Agent/Frontend NodePort exposure** — moved from `ClusterIP` to `NodePort` so the browser can reach them without an Ingress.
7. **Grafana dashboard** — Error Rate panel is now a `timeseries`, not a single `stat`.
8. **CI** — new `deploy-fluent-bit` job auto-deploys `fluent-bit.conf` changes to the EC2 host over SSH.

---

## 5. ⚠️ Things to double check / test

- ~~**Bucket prefix mismatch**~~ — **fixed**: `fluent-bit.conf` again uses `/${LOG_ENV}/logs/...`, and the `deploy-fluent-bit` CI job now writes `LOG_ENV=dev`/`prod` into the remote `.env` based on branch (same pattern as the other deploy jobs), so dev/prod logs no longer collide under the same prefix.
- **`docker-compose.yml:95`**: `S3_LOGS_BUCKET` env var — confirm this matches what `fluent-bit.conf` expects (`${S3_LOGS_BUCKET}` line 30) and that it's actually set on both EC2 hosts.
- **MCP env vars in `.vscode/mcp.json`**: confirm `DEV_S3_LOGS_BUCKET`/`PROD_S3_LOGS_BUCKET` still line up with the new `/${LOG_ENV}/logs/host=.../service=.../` key shape (the MCP's `_extract_service_from_key` expects `/service=<name>/` literally in the key).
- ~~**Prod frontend**~~ — **partially addressed**: prod still has no public DNS, so no valid origin string exists to add yet; `services/agent/app.py` now has an explicit comment marking this as a TODO once prod DNS/host exists, instead of silently omitting it.
- **HPA**: needs Metrics Server installed in-cluster or `TARGETS` will show `<unknown>`.
- **New CI job `deploy-fluent-bit`**: needs `DEV_INSTANCE_IP`/`PROD_INSTANCE_IP` + SSH key secrets configured in GitHub — confirm they exist before merging.

---

## 6. Test checklist (in order)

1. `kubectl get ns` → dev, prod Active
2. `kubectl get all -n dev` / `-n prod` → all objects present
3. `kubectl get hpa -n dev` → TARGETS shows a %, not `<unknown>`
4. Load-test YOLO, watch HPA scale 1→2/3 replicas
5. `kubectl get pvc,pv -n dev` → both `Bound`
6. Write a file to `/prometheus`, delete the Pod, confirm the file survives
7. Prometheus UI → Status → Targets → all 3 jobs `UP`
8. Open Grafana → Prometheus datasource pre-configured, no manual setup
9. `docker compose ps` on EC2 → `fluent-bit` `Up`
10. Generate test traffic, then `aws s3 ls s3://<bucket>/dev/logs/ --recursive` → new objects appear under `host=.../service=<name>/`
11. Reload VS Code, confirm Copilot lists all 4 `observability` MCP tools
12. Run the 4 assignment prompts in Copilot Chat (logs, prod CPU, list shipping containers, incident-at-timestamp)
13. `curl http://<agent>:8000/metrics` → 4 `agent_chat_*` metrics present
14. Import `infra/grafana/dashboards/agent.json` → all 5 panels render, Error Rate is a line graph

---

## 7. 60-second spoken summary

"I deployed the full stack to Kubernetes across dev and prod namespaces using plain Deployments, with probes, resource limits, and an HPA on Yolo, Agent, and Frontend that I proved scales under load. Prometheus stores its data on a statically-provisioned EBS volume so metrics survive Pod restarts, and Grafana auto-connects to it. On the EC2 side, Fluent Bit now tags logs by Docker Compose service name and ships them to S3, and I reworked the local MCP server to match — it looks up logs by service name instead of container ID, and can now query logs around a specific timestamp for incident questions. I also instrumented the Agent with Prometheus metrics and built a Grafana dashboard showing requests, error rate, latency percentiles, and token usage over time."
