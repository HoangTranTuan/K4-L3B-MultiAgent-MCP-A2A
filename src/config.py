import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

ENV_FILE = BASE_DIR / ".env"

load_dotenv(ENV_FILE)


class Config:
    APP_ENV = os.getenv("APP_ENV", "development")
    DEBUG = os.getenv("DEBUG", "false").lower() == "true"

    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")

    MCP_SERVER_URL = os.getenv(
        "MCP_SERVER_URL",
        "http://localhost:8000",
    )

    INPUT_DIR = BASE_DIR / os.getenv("INPUT_DIR", "inputs")
    OUTPUT_DIR = BASE_DIR / os.getenv("OUTPUT_DIR", "outputs")
    TRACE_DIR = BASE_DIR / os.getenv("TRACE_DIR", "traces")

    REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "30"))
    MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))

    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

    @classmethod
    def validate(cls):
        errors = []

        if not cls.OPENAI_API_KEY:
            errors.append("OPENAI_API_KEY chưa được cấu hình")

        if cls.REQUEST_TIMEOUT <= 0:
            errors.append("REQUEST_TIMEOUT phải lớn hơn 0")

        if cls.MAX_RETRIES < 0:
            errors.append("MAX_RETRIES không được âm")

        if errors:
            raise ValueError(
                "Lỗi cấu hình:\n- " + "\n- ".join(errors)
            )


config = Config()