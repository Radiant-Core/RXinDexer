# Configuration management for RXinDexer
# Uses Pydantic for validation and automatic environment variable loading
import os
from typing import Optional
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """
    Application settings with environment variable validation.
    All settings are loaded from environment variables with validation.
    Missing required variables will raise clear error messages at startup.
    """
    
    # Radiant Node Configuration
    RADIANT_NODE_HOST: str = Field(default="radiant-node", description="Radiant node hostname")
    RADIANT_NODE_RPCUSER: str = Field(default="dockeruser", description="RPC username for Radiant node")
    RADIANT_NODE_RPCPASSWORD: str = Field(default="dockerpass", description="RPC password for Radiant node")
    RADIANT_NODE_RPCPORT: int = Field(default=7332, ge=1, le=65535, description="RPC port")
    RADIANT_NODE_RESTPORT: int = Field(default=7333, ge=1, le=65535, description="REST port")
    
    # PostgreSQL Configuration
    POSTGRES_HOST: str = Field(default="db", description="PostgreSQL hostname")
    POSTGRES_PORT: int = Field(default=5432, ge=1, le=65535, description="PostgreSQL port")
    POSTGRES_DB: str = Field(default="rxindexer", description="Database name")
    POSTGRES_USER: str = Field(default="rxindexer", description="Database user")
    POSTGRES_PASSWORD: str = Field(default="dsUEZPX1mqwPhRlicEGbjhERjioXqgdcvoEKCZMkwLc=", description="Database password")
    
    # API Configuration
    API_SECRET_KEY: Optional[str] = Field(default=None, description="JWT secret key (auto-generated if not set)")
    API_DEBUG: bool = Field(default=False, description="Enable debug mode")
    
    # Indexer Configuration
    ENABLE_AUTOMATED_BACKFILLS: bool = Field(default=True, description="Enable automated backfills")
    BACKFILL_MAX_SYNC_LAG: int = Field(default=10, ge=1, description="Max sync lag before pausing backfills")
    
    @field_validator('RADIANT_NODE_RPCPASSWORD', 'POSTGRES_PASSWORD')
    @classmethod
    def password_not_empty(cls, v: str) -> str:
        if not v:
            raise ValueError('Password cannot be empty')
        return v
    
    @property
    def database_url(self) -> str:
        """Construct PostgreSQL connection URL."""
        return f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
    
    @property
    def rpc_url(self) -> str:
        """Construct Radiant node RPC URL."""
        return f"http://{self.RADIANT_NODE_RPCUSER}:{self.RADIANT_NODE_RPCPASSWORD}@{self.RADIANT_NODE_HOST}:{self.RADIANT_NODE_RPCPORT}"
    
    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": True,
        "extra": "ignore",  # Ignore extra environment variables
    }


# Lazy initialization to allow graceful handling of missing env vars during import
_settings: Optional[Settings] = None


def get_settings() -> Settings:
    """Get validated settings, raising clear errors for missing required variables."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


# For backward compatibility - will validate on first access
settings = get_settings()
