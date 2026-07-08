# 11 - Monitoring

## Prometheus
- configured in prometheus.yml.
- scrapes yolo target every 15 seconds.

## Grafana
- runs on host port 3001.
- connects to Prometheus as datasource.

## Monitoring path
```mermaid
flowchart LR
  YO[YOLO metrics] --> PR[Prometheus]
  PR --> GF[Grafana dashboards]
```

## Why this setup
- Prometheus is optimized for scrape + time-series storage.
- Grafana is optimized for visualization and dashboarding.
