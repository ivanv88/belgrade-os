from sqlmodel import create_engine, Session, SQLModel
import os
from dotenv import load_dotenv

load_dotenv()

# We use the internal Docker network if FastAPI was in Docker, 
# but since FastAPI is running on the Pi "bare metal" for now, we use localhost.
DB_USER = "laurent"
DB_PASS = os.getenv("DB_PASSWORD")
DB_NAME = "belgrade_os"
DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASS}@localhost:5432/{DB_NAME}"

engine = create_engine(DATABASE_URL, echo=False)

def init_db():
    # This creates the tables if they don't exist
    SQLModel.metadata.create_all(engine)

def get_session():
    with Session(engine) as session:
        yield session
