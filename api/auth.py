from datetime import datetime, timedelta
from typing import Optional, Union
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel
import os
import secrets

# Configuration - Load from environment or auto-generate secure key
_env_secret = os.getenv("API_SECRET_KEY", "")
if not _env_secret or len(_env_secret) < 32:
    # Auto-generate a secure key if not provided (will change on restart)
    # For production, always set API_SECRET_KEY in environment
    _env_secret = secrets.token_urlsafe(32)
    import logging
    logging.warning("API_SECRET_KEY not set or too short. Using auto-generated key. Set API_SECRET_KEY for persistent sessions.")

SECRET_KEY = _env_secret
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))

# Security
security = HTTPBearer()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class Token(BaseModel):
    access_token: str
    token_type: str


class TokenData(BaseModel):
    username: Optional[str] = None


class User(BaseModel):
    username: str
    active: bool = True


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash."""
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """Generate password hash."""
    return pwd_context.hash(password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    """Create JWT access token."""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Get current user from JWT token."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
        token_data = TokenData(username=username)
    except JWTError:
        raise credentials_exception
    
    # In a real implementation, you would fetch the user from database
    # For now, we'll use a simple check
    user = get_user(username=token_data.username)
    if user is None:
        raise credentials_exception
    return user


async def get_current_active_user(current_user: User = Depends(get_current_user)):
    """Get current active user."""
    if not current_user.active:
        raise HTTPException(status_code=400, detail="Inactive user")
    return current_user


def get_user(username: str) -> Optional[User]:
    """
    Get user from database.
    This is a placeholder implementation.
    In production, you would fetch from your database.
    """
    # For now, return a dummy user if username is "admin"
    if username == "admin":
        return User(username=username, active=True)
    return None


def authenticate_user(username: str, password: str) -> Union[User, bool]:
    """
    Authenticate user.
    This is a placeholder implementation.
    In production, you would verify against database.
    """
    user = get_user(username)
    if not user:
        return False
    # For demo purposes, accept "admin" / "admin" credentials
    # In production, use: return user if verify_password(password, user.hashed_password) else False
    if username == "admin" and password == "admin":
        return user
    return False


# Optional: Create a dependency for optional authentication (public endpoints)
async def get_current_user_optional(credentials: Optional[HTTPAuthorizationCredentials] = Depends(HTTPBearer(auto_error=False))):
    """Get current user from JWT token, but don't raise error if missing."""
    if credentials is None:
        return None
    
    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            return None
        token_data = TokenData(username=username)
    except JWTError:
        return None
    
    user = get_user(username=token_data.username)
    return user
