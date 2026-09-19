from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from sqlalchemy.orm import Session
from pydantic import BaseModel

from ..data_transfer import ImportFormatError, apply_import, export_csv, export_json, load_user_people, parse_upload
from ..database import get_db
from ..dependencies import get_current_user
from ..models import EventType, User

router = APIRouter(prefix="/data", tags=["data"])

MAX_IMPORT_BYTES = 2 * 1024 * 1024


class ImportOut(BaseModel):
    people_created: int
    events_added: int
    events_skipped: int
    event_types_created: int
    invalid_rows: int
    errors: list[str]


@router.get("/export")
def export_data(
    format: Literal["csv", "json"] = "json",
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    people = load_user_people(db, current_user.id)
    if format == "csv":
        body, media_type = export_csv(people), "text/csv; charset=utf-8"
    else:
        event_types = (
            db.query(EventType).filter(EventType.user_id == current_user.id).order_by(EventType.sort_order).all()
        )
        body, media_type = export_json(people, event_types), "application/json"
    filename = f"candlr-export-{date.today().isoformat()}.{format}"
    return Response(
        content=body,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"},
    )


@router.post("/import", response_model=ImportOut)
def import_data(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    raw = file.file.read(MAX_IMPORT_BYTES + 1)
    if len(raw) > MAX_IMPORT_BYTES:
        raise HTTPException(status_code=413, detail="File is too large (2 MB max)")
    try:
        parsed = parse_upload(file.filename or "", raw)
    except ImportFormatError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not parsed.people:
        detail = "No valid rows found"
        if parsed.errors:
            detail += f". {parsed.errors[0]}"
        raise HTTPException(status_code=400, detail=detail)
    return apply_import(db, current_user.id, parsed)
