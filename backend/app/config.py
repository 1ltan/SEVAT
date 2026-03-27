from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    gemini_api_key: str
    gemini_model: str = "gemini-2.5-flash"
    gemini_embedding_model: str = "models/embedding-001"

    postgres_user: str
    postgres_password: str
    postgres_db: str = "DetectionDangers"
    postgres_host: str = "postgres"
    postgres_port: int = 5432

    screenshot_dir: str = "/data/screenshots"
    model_path: str = "/app/runs/detect/train/weights/best.pt"
    frame_skip: int = 5
    jpeg_quality: int = 60
    stream_fps: int = 10

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def sync_database_url(self) -> str:
        return (
            f"postgresql+psycopg2://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()
