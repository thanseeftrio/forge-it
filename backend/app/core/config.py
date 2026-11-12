import os
from pathlib import Path
from typing import List, Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    PROJECT_NAME: str = "FORGE IT Backend"
    VERSION: str = "0.1.0"
    ENVIRONMENT: str = "development"
    DEBUG: bool = True

    # Server configuration
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    API_V1_STR: str = "/api"

    # CORS origins for frontend dev servers
    CORS_ORIGINS: List[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]

    # Workspace directory: default to root of Forge it repo
    BASE_DIR: Path = Path(__file__).resolve().parent.parent.parent.parent
    
    # Tool paths (can be overridden via environment variables)
    KICAD_CLI_PATH: str = "kicad-cli"
    VERILATOR_PATH: str = "verilator"

    # Execution safeguards
    DEFAULT_TIMEOUT_SECONDS: int = 30
    MAX_TIMEOUT_SECONDS: int = 120

    # AI Agent settings
    GEMINI_API_KEY: Optional[str] = None
    GEMINI_MODEL: str = "gemini-3.1-flash-lite"

    # Permitted operations
    ALLOWED_OPERATIONS: List[str] = [
        "inspect_project",
        "kicad_drc",
        "kicad_erc",
        "kicad_export_gerbers",
        "verilator_lint",
        "verilator_compile",
    ]

    model_config = SettingsConfigDict(
        env_file=(".env", "backend/.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
