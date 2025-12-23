# Configuration management for RXinDexer
import os

class Settings:
    RADIANT_NODE_HOST: str = os.getenv("RADIANT_NODE_HOST", "localhost")
    RADIANT_NODE_RPCUSER: str = os.getenv("RADIANT_NODE_RPCUSER", "dockeruser")
    RADIANT_NODE_RPCPASSWORD: str = os.getenv("RADIANT_NODE_RPCPASSWORD", "dockerpass")
    RADIANT_NODE_RPCPORT: int = int(os.getenv("RADIANT_NODE_RPCPORT", 7332))
    RADIANT_NODE_RESTPORT: int = int(os.getenv("RADIANT_NODE_RESTPORT", 7333))

settings = Settings()
