from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import HealthOut

router = APIRouter(tags=["Health"])


@router.get(
    "/health",
    response_model=HealthOut,
    summary="Liveness and database connectivity check",
    responses={503: {"model": HealthOut, "description": "Database unreachable"}},
)
def health(db: Session = Depends(get_db)) -> HealthOut | JSONResponse:
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError:
        return JSONResponse(status_code=503, content={"status": "degraded", "database": "down"})
    return HealthOut(status="ok", database="up")
