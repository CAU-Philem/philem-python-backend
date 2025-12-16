# philem-python-backend/config/settings.py

from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # OPENAI
    openai_api_keys: list[str]
    openai_model: str
    openai_workers: int
    worker_sleep_seconds: int

    # SSH
    use_ssh_tunnel: bool
    ssh_host: str
    ssh_user: str   
    ssh_key_path: str

    # DB
    db_host: str
    db_port: int
    db_user: str
    db_password: str
    db_name: str

    kakao_rest_key: str
    database_url: str

    class Config:
        env_file = ".env"  # load environment variables if present

settings = Settings()
