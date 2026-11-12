# FORGE IT Architecture

```
                  +-----------------------------------+
                  |         Frontend (Vite+React)     |
                  |               Member 2            |
                  +-----------------+-----------------+
                                    | REST / SSE
                                    v
                  +-----------------------------------+
                  |         Backend (FastAPI)         |
                  |               Member 1            |
                  +--------+-----------------+--------+
                           |                 |
                +----------v-------+  +------v-----------+
                |     AI Agent     |  |   Integrations   |
                |     Member 3     |  |   Members 4 & 5  |
                |   (Gemini Pro)   |  | (KiCad / Verilog)|
                +------------------+  +------------------+
```

