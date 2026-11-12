# API Contract

Backend endpoints and schemas agreed between Member 1, Member 2, and Member 3.

## Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/health` | Backend status check |
| `POST` | `/api/projects/upload` | Upload KiCad zip or Verilog files |
| `GET` | `/api/projects/{project_id}` | Fetch project file tree & details |
| `POST` | `/api/agent/run` | Submit analysis task for execution |
| `GET` | `/api/agent/jobs/{job_id}` | Poll job execution status |
| `GET` | `/api/integrations/status` | Check local EDA tool availability |

