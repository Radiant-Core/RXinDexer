from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from api.dependencies import get_db
from api.schemas import UserProfileResponse
from database.queries import get_user_profile

router = APIRouter()

@router.get("/users/{address}", response_model=UserProfileResponse, summary="User profile by address")
def get_user_profile_api(address: str, db: Session = Depends(get_db)):
    profile = get_user_profile(db, address)
    if not profile:
        raise HTTPException(status_code=404, detail="User profile not found")
    return UserProfileResponse(
        address=profile.address,
        containers=profile.containers,
        created_at=profile.created_at.isoformat() if profile.created_at else None
    )
