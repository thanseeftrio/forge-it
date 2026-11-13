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

---

## Member 5: IC / RTL Integration Subsystem

FORGE IT connects to existing EDA tools rather than implementing a new design editor. The IC Integration Subsystem manages detection, project inspection, and safe invocation of RTL toolchains (Icarus Verilog and Verilator).

### Subsystem Component Breakdown

```
+-----------------------------------------------------------------------------------+
|                        integrations/ic/ (ICAdapter)                               |
|                                                                                   |
|  +--------------------+   +---------------------------+   +--------------------+  |
|  |     ICDetector     |   |   RTLProjectInspector     |   |    ICOperations    |  |
|  |--------------------|   |---------------------------|   |--------------------|  |
|  | - Tool discovery   |   | - File tree scanning      |   | - Op allowlist     |  |
|  | - Version retrieval|   | - Module parsing          |   | - subprocess exec  |  |
|  | - Env sanitization |   | - Top candidate detection |   | - Diagnostic parser|  |
|  +--------------------+   +---------------------------+   +--------------------+  |
|            |                           |                           |              |
+------------|---------------------------|---------------------------|--------------+
             |                           |                           |
             v                           v                           v
      [iverilog -V]                [Static Scan]           [iverilog -t null / -o]
```

### Security & Isolation Principles
1. **No Arbitrary Shell Execution**: All external commands are executed with `shell=False` through parameter lists. AI agents cannot inject arbitrary shell commands.
2. **Strict Operation Allowlist**: Only predefined operations (`lint`, `syntax_check`, `compile`, `simulate`) are permitted.
3. **Bounded Execution**: Every tool process is guarded by configurable timeouts (`timeout=30s`) to prevent runaway processes or infinite loops.
4. **Project Integrity**: Source files are treated as read-only. Compilation artifacts are segregated into `.forge_build/` to prevent polluting or modifying user design repositories.
5. **Truth in Diagnostics**: Raw stderr and stdout streams are faithfully captured and parsed into line-specific issues without fabricating synthetic successes.
