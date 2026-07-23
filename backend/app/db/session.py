"""
Database connection setup using SQLAlchemy + SQLite.

Why SQLite: zero setup needed (it's just a file), which keeps focus on the
sync logic rather than database administration. See docs/TECHNICAL_ARCHITECTURE.md
for the reasoning and future migration path to MySQL/Postgres.
"""

from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# SQLite file will be created in the backend/ folder on first run
SQLALCHEMY_DATABASE_URL = "sqlite:///./database.db"

# check_same_thread=False is needed only for SQLite, since FastAPI may use
# the connection across different threads
engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """
    Dependency used by FastAPI routes to get a database session.
    Ensures the session is always closed after the request finishes.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
