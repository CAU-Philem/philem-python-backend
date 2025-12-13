# philem-python-backend/config/settings.py

from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # OPENAI
    openai_api_keys: list[str]

    # SSH
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
