# api/deps.py

from typing import Generator
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

from config.settings import get_settings

settings = get_settings()

# Build a SQLAlchemy engine for direct DB access (NOT via SSH tunnel).
# If your FastAPI server runs in an environment where it needs SSH tunneling
# to reach your DB, you'll modify this layer later. For now, use direct host.
DATABASE_URL = (
    f"mysql+pymysql://{settings.db_user}:{settings.db_password}"
    f"@{settings.db_host}/{settings.db_name}"
)

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,     # avoids stale connections
    pool_recycle=1800,      # recycles connection after 30 min
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)


def get_db() -> Generator[Session, None, None]:
    """
    Dependency for FastAPI routes.
    Provides a SQLAlchemy session that is automatically closed
    after the request is done, even if an exception occurs.
    
    Usage:
        @router.get("/items")
        def list_items(db: Session = Depends(get_db)):
            return db.query(Item).all()
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
