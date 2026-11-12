# FORGE IT

> AI Engineering Assistant for PCB and IC Workflows.

FORGE IT connects hardware engineers and students with specialized AI and EDA tool workflows (KiCad DRC/ERC analysis, Verilog/RTL linting & simulation) via a unified interface.

## Repository Structure

```
forge-it/
├── frontend/                       # React + TypeScript + Vite (Member 2)
│   ├── public/
│   ├── src/
│   │   ├── assets/
│   │   ├── components/
│   │   │   ├── Sidebar/
│   │   │   ├── Dashboard/
│   │   │   ├── Chat/
│   │   │   ├── Integrations/
│   │   │   └── ProjectExplorer/
│   │   ├── pages/
│   │   ├── layouts/
│   │   ├── services/
│   │   │   └── api.ts
│   │   ├── hooks/
│   │   ├── App.tsx
│   │   └── main.tsx
│   ├── package.json
│   └── vite.config.ts
│
├── backend/                        # FastAPI Backend (Member 1)
│   ├── app/
│   │   ├── api/routes/
│   │   │   ├── health.py
│   │   │   ├── integrations.py
│   │   │   ├── projects.py
│   │   │   └── agent.py
│   │   ├── core/
│   │   ├── schemas/
│   │   ├── services/
│   │   └── integrations/
│   ├── tests/
│   ├── requirements.txt
│   └── .env.example
│
├── ai-agent/                       # AI Agent & Reasoning (Member 3)
│   ├── agent.py
│   ├── planner.py
│   ├── prompts.py
│   ├── provider.py
│   ├── tool_registry.py
│   ├── result_analyzer.py
│   └── tests/
│
├── integrations/                   # EDA Adapters (Members 4 & 5)
│   ├── pcb/                        # Member 4 - KiCad Adapter
│   └── ic/                         # Member 5 - IC / Verilator Adapter
│
├── examples/                       # Sample test fixtures
├── docs/                           # Architecture and API contracts
└── scripts/                        # Setup and start scripts
```

## Quick Start

1. Run setup:
   ```bash
   ./scripts/setup.sh
   ```
2. Start services:
   ```bash
   ./scripts/start.sh
   ```

