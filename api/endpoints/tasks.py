"""
API endpoints for background task management.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Optional

from api.dependencies import get_current_authenticated_user
from api.auth import User

router = APIRouter(tags=["tasks"])


@router.get("/tasks/status", summary="Get task queue status")
async def get_task_queue_status():
    """Get the status of the background task queue."""
    from api.tasks.worker import get_queue_stats
    return await get_queue_stats()


@router.post("/tasks/holder-counts", summary="Trigger holder count update")
async def trigger_holder_count_update(
    token_ref: Optional[str] = Query(None, description="Specific token ref to update"),
    user: User = Depends(get_current_authenticated_user)
):
    """
    Trigger a holder count update task.
    
    Requires authentication.
    """
    from api.tasks.worker import enqueue_task
    
    job_id = await enqueue_task("update_holder_counts", token_ref=token_ref)
    
    if job_id:
        return {"status": "queued", "job_id": job_id, "token_ref": token_ref}
    else:
        return {"status": "queue_unavailable", "message": "Task queue not available, task will run on next scheduled cycle"}


@router.post("/tasks/refresh-balances", summary="Trigger balance refresh")
async def trigger_balance_refresh(
    address: Optional[str] = Query(None, description="Specific address to refresh"),
    user: User = Depends(get_current_authenticated_user)
):
    """
    Trigger a wallet balance refresh task.
    
    Requires authentication.
    """
    from api.tasks.worker import enqueue_task
    
    job_id = await enqueue_task("refresh_balances", address=address)
    
    if job_id:
        return {"status": "queued", "job_id": job_id, "address": address}
    else:
        return {"status": "queue_unavailable", "message": "Task queue not available"}


@router.post("/tasks/refresh-metadata", summary="Trigger token metadata refresh")
async def trigger_metadata_refresh(
    token_ref: str = Query(..., description="Token ref to refresh"),
    user: User = Depends(get_current_authenticated_user)
):
    """
    Trigger a token metadata refresh task.
    
    Requires authentication.
    """
    from api.tasks.worker import enqueue_task
    
    job_id = await enqueue_task("refresh_token_metadata", token_ref=token_ref)
    
    if job_id:
        return {"status": "queued", "job_id": job_id, "token_ref": token_ref}
    else:
        return {"status": "queue_unavailable", "message": "Task queue not available"}


@router.post("/tasks/cleanup", summary="Trigger data cleanup")
async def trigger_cleanup(
    days: int = Query(30, ge=7, le=365, description="Days to retain"),
    user: User = Depends(get_current_authenticated_user)
):
    """
    Trigger a data cleanup task.
    
    Requires authentication.
    """
    from api.tasks.worker import enqueue_task
    
    job_id = await enqueue_task("cleanup_old_data", days=days)
    
    if job_id:
        return {"status": "queued", "job_id": job_id, "days": days}
    else:
        return {"status": "queue_unavailable", "message": "Task queue not available"}
