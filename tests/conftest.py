import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient
from unittest.mock import MagicMock
import sys
import os

# Add project root to python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.models import Base
from api.main import app
from api.dependencies import get_db

# Use SQLite for testing
SQLALCHEMY_DATABASE_URL = "sqlite:///./test.db"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

@pytest.fixture(scope="module")
def db_engine():
    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)
    os.remove("./test.db")

@pytest.fixture(scope="function")
def db(db_engine):
    connection = db_engine.connect()
    transaction = connection.begin()
    
    # Configure the global SessionLocal to use our test connection
    import database.session
    # Save original bind
    original_bind = database.session.SessionLocal.kw.get('bind')
    # Reconfigure
    database.session.SessionLocal.configure(bind=connection)
    
    session = database.session.SessionLocal()
    
    yield session
    
    session.close()
    # Restore original bind (though likely not strictly necessary for single test run)
    # database.session.SessionLocal.configure(bind=original_bind) 
    
    transaction.rollback()
    connection.close()

@pytest.fixture(scope="function")
def client(db):
    def override_get_db():
        try:
            yield db
        finally:
            pass
    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    del app.dependency_overrides[get_db]

@pytest.fixture
def mock_rpc(mocker):
    mock = mocker.patch("api.utils.requests.post")
    return mock
