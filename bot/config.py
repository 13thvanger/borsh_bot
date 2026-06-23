from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    bot_token: str
    database_url: str

    agent_url: str | None = None
    agent_api_key: str | None = None
    agent_model: str = "cifra48/agent"
    agent_timeout_seconds: int = 60
    agent_required: bool = False
    borsh_photo_window_minutes: int = 15

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def agent_enabled(self) -> bool:
        return bool((self.agent_url or "").strip() and (self.agent_api_key or "").strip())

    @property
    def agent_disabled_reason(self) -> str:
        missing = []
        if not (self.agent_url or "").strip():
            missing.append("AGENT_URL")
        if not (self.agent_api_key or "").strip():
            missing.append("AGENT_API_KEY")
        if not missing:
            return ""
        return "не задано: " + ", ".join(missing)


settings = Settings()
