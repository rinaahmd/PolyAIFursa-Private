# PolyAI

## Setup

Create and activate a virtual environment from the repo root directory:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Your terminal prompt should now show `(.venv)`. Keep this environment active whenever you run any service.

See each service's README for how to configure and run it.

## Docker Quick Run

YOLO:

```bash
docker run --rm --name yolo \
	-v ~/.aws:/root/.aws:ro \
	-e AWS_REGION=us-east-1 \
	-e AWS_S3_BUCKET=rina-polyai-images \
	-p 8080:8080 \
	rinaahmd/yolo-service:0.0.1
```

Agent (Linux needs host-gateway mapping):

```bash
docker run --rm --name agent \
	--env-file services/agent/.env \
	--add-host=host.docker.internal:host-gateway \
	-v ~/.aws:/root/.aws:ro \
	-p 8000:8000 \
	rinaahmd/agent-service:0.0.1
```

Ensure `services/agent/.env` sets `YOLO_SERVICE_URL=http://host.docker.internal:8080` for docker-run deployments.

Frontend:

```bash
docker run --rm --name frontend \
	-e NEXT_PUBLIC_AGENT_URL=http://host.docker.internal:8000 \
	--add-host=host.docker.internal:host-gateway \
	-p 3000:3000 \
	rinaahmd/frontend-service:0.0.1
```

GitHub Actions deploy workflow filename is `.github/workflows/deploy.yaml`.