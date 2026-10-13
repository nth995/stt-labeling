"""Lưu/Tải CSV theo quyền dataset và theo dõi phiên làm việc."""

import logging
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.routes.labeling import request_context, protected_form
from app.services.activity_service import finish_activity
from app.services.csv_sync_service import sync_dataset_csv
from app.services.labeling_service import LabelingError, ensure_samples

router = APIRouter(prefix="/labeling")
LOGGER = logging.getLogger(__name__)


def _permission(request: Request, db: Session, dataset_id: int, token: str):
    """Kiểm tra phiên, quyền dataset và CSRF trước khi ghi CSV."""
    context, dataset, error = request_context(request, db, dataset_id)
    if error is not None:
        return None, None, error
    rejected = protected_form(context, token)
    if rejected is not None:
        return None, None, rejected
    return context, dataset, None


@router.post("/{dataset_id}/save-csv")
def save_csv(
    dataset_id: int, request: Request, csrf_token: str = Form(...),
    sample_id: int | None = Form(default=None), db: Session = Depends(get_db),
):
    """Admin/Annotator cùng có quyền xuất snapshot mới nhất của dataset."""
    context, dataset, error = _permission(request, db, dataset_id, csrf_token)
    if error is not None:
        return error
    try:
        ensure_samples(db, dataset)
        result = sync_dataset_csv(dataset_id)
    except (OSError, ValueError, LabelingError) as exc:
        message = f"Không thể lưu CSV: {exc}"
        key = "error"
    else:
        key = "message"
        message = ("CSV đã đồng bộ; không có thay đổi mới." if result.skipped
                   else f"Đã lưu {result.sample_count} mẫu vào CSV (phiên bản {result.exported_version}).")
        if result.latest_version > result.exported_version:
            message += " Có nhãn mới phát sinh trong lúc xuất; hãy nhấn Lưu CSV lần nữa."
    params = {key: message}
    if sample_id is not None:
        params["sample_id"] = str(sample_id)
    return RedirectResponse(f"/labeling/{dataset_id}?{urlencode(params)}", status_code=303)


@router.post("/{dataset_id}/download-csv")
def download_csv(
    dataset_id: int, request: Request, csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    """Tải bản CSV đã đồng bộ từ SQLite; không đọc dữ liệu của dataset khác."""
    context, dataset, error = _permission(request, db, dataset_id, csrf_token)
    if error is not None:
        return error
    try:
        ensure_samples(db, dataset)
        result = sync_dataset_csv(dataset_id)
    except (OSError, ValueError, LabelingError) as exc:
        return HTMLResponse(f"Không thể tải CSV: {exc}", status_code=500)
    return FileResponse(
        result.path, filename=f"{dataset.folder_name}_label_reviewed.csv",
        media_type="text/csv", headers={"Cache-Control": "no-store"},
    )


@router.post("/{dataset_id}/session-end")
def session_end(
    dataset_id: int, request: Request, csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    """Best-effort Beacon khi đóng tab; heartbeat timeout là phương án dự phòng."""
    context, dataset, error = _permission(request, db, dataset_id, csrf_token)
    if error is not None:
        return error
    try:
        finish_activity(db, context.session.id, dataset_id)
    except (OSError, ValueError) as exc:
        LOGGER.warning("Chưa thể xuất CSV khi đóng tab: %s", exc)
        return JSONResponse({"ok": False}, status_code=503)
    return JSONResponse({"ok": True})
