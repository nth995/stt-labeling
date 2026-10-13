"""Trang gán nhãn và API phát audio, điều phối khóa theo phiên đăng nhập."""

from pathlib import Path
import time
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Header, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.database.models import Sample, SampleLock
from app.services.auth_service import get_auth_context, get_csrf_token, is_valid_csrf
from app.services.dataset_service import checked_audio_file, dataset_directory
from app.services.labeling_service import (
    LabelingError, allowed_dataset, claim_next, confirm_sample, dataset_counts,
    ensure_samples, release_lease, renew_lease, require_lease, sample_page, try_claim,
)

router = APIRouter(prefix="/labeling")
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "templates"))


def request_context(request: Request, db: Session, dataset_id: int):
    """Yêu cầu phiên đăng nhập và quyền dataset ở mọi đường dẫn."""
    context = get_auth_context(request, db)
    if context is None:
        return None, None, RedirectResponse("/login", status_code=303)
    try:
        dataset = allowed_dataset(db, dataset_id, context.user)
    except LabelingError as error:
        return None, None, HTMLResponse(str(error), status_code=403)
    return context, dataset, None


def protected_form(context, token: str):
    """Từ chối biểu mẫu giả mạo có ý định thay đổi SQLite."""
    if not is_valid_csrf(context, token):
        return HTMLResponse("Yêu cầu không hợp lệ.", status_code=403)
    return None


def redirect_page(dataset_id: int, **params):
    """Trở lại giao diện gán nhãn với thông báo hoặc mẫu vừa nhận."""
    query = f"?{urlencode(params)}" if params else ""
    return RedirectResponse(f"/labeling/{dataset_id}{query}", status_code=303)


def load_sample(db: Session, dataset_id: int, sample_id: int) -> Sample:
    """Không cho truy cập sample_id thuộc dataset khác."""
    sample = db.get(Sample, sample_id)
    if sample is None or sample.dataset_id != dataset_id:
        raise LabelingError("Không tìm thấy audio trong dataset này.")
    return sample


def labeling_page_response(
    request: Request, db: Session, context, dataset,
    current_sample: Sample | None, *, error: str | None = None,
    draft_label: str | None = None, status: str = "pending", page: int = 1,
):
    """Render cùng một giao diện cho nghe, sửa, xác nhận và kiểm tra lại."""
    samples = sample_page(db, dataset.id, status, page)
    active_lock = None
    if current_sample:
        active_lock = db.get(SampleLock, current_sample.id)
    return templates.TemplateResponse(
        request=request,
        name="labeling.html",
        context={
            "user": context.user,
            "csrf_token": get_csrf_token(context),
            "dataset": dataset,
            "counts": dataset_counts(db, dataset.id),
            "samples": samples,
            "current": current_sample,
            "can_edit": bool(active_lock and active_lock.session_id == context.session.id
                             and active_lock.expires_at > time.time()),
            "draft_label": draft_label,
            "error": error or request.query_params.get("error"),
            "message": request.query_params.get("message"),
            "filter_status": status,
            "page": page,
        },
    )


@router.get("/{dataset_id}", response_class=HTMLResponse)
def labeling_page(
    dataset_id: int, request: Request, sample_id: int | None = None,
    status: str = "pending", page: int = 1,
    db: Session = Depends(get_db),
):
    """Hiển thị danh sách và mẫu đã được phiên hiện tại nhận."""
    context, dataset, error = request_context(request, db, dataset_id)
    if error is not None:
        return error
    if status not in ("pending", "confirmed", "all"):
        status = "pending"
    page = max(1, min(page, 1_000_000))
    try:
        ensure_samples(db, dataset)
        sample = load_sample(db, dataset_id, sample_id) if sample_id else None
    except LabelingError as exc:
        return labeling_page_response(request, db, context, dataset, None, error=str(exc), status=status, page=page)
    return labeling_page_response(request, db, context, dataset, sample, status=status, page=page)


@router.post("/{dataset_id}/next")
def next_sample(
    dataset_id: int, request: Request, csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    """Tự cấp audio pending tiếp theo."""
    context, dataset, error = request_context(request, db, dataset_id)
    if error is not None:
        return error
    rejected = protected_form(context, csrf_token)
    if rejected is not None:
        return rejected
    try:
        ensure_samples(db, dataset)
        sample = claim_next(db, dataset_id, context.user.id, context.session.id)
    except LabelingError as exc:
        return redirect_page(dataset_id, error=str(exc))
    if sample is None:
        return redirect_page(dataset_id, message="Không còn mẫu pending chưa được người khác giữ.")
    return redirect_page(dataset_id, sample_id=sample.id)


@router.post("/{dataset_id}/open")
def open_sample(
    dataset_id: int, request: Request,
    sample_id: int = Form(...), csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    """Nhận mẫu được chọn thủ công, kể cả confirmed cần sửa lại."""
    context, dataset, error = request_context(request, db, dataset_id)
    if error is not None:
        return error
    rejected = protected_form(context, csrf_token)
    if rejected is not None:
        return rejected
    try:
        ensure_samples(db, dataset)
        sample = load_sample(db, dataset_id, sample_id)
        success = try_claim(db, sample, context.user.id, context.session.id)
    except LabelingError as exc:
        return redirect_page(dataset_id, error=str(exc))
    if not success:
        return redirect_page(dataset_id, error="Audio đang được người khác giữ. Vui lòng chọn mẫu khác.")
    return redirect_page(dataset_id, sample_id=sample_id)


@router.get("/{dataset_id}/audio/{sample_id}")
def audio_file(
    dataset_id: int, sample_id: int, request: Request,
    db: Session = Depends(get_db),
):
    """Phục vụ đúng file audio được CSV tham chiếu, hỗ trợ HTTP Range."""
    context, dataset, error = request_context(request, db, dataset_id)
    if error is not None:
        return error
    try:
        sample = load_sample(db, dataset_id, sample_id)
        directory = dataset_directory(dataset.folder_name)
        if not checked_audio_file(directory, sample.audio_path):
            raise LabelingError("Không tìm thấy file audio hoặc đường dẫn không hợp lệ.")
    except LabelingError as exc:
        return HTMLResponse(str(exc), status_code=404)
    path = directory / sample.audio_path.replace("\\", "/")
    response = FileResponse(path, content_disposition_type="inline")
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "private, max-age=60"
    return response


@router.post("/{dataset_id}/heartbeat/{sample_id}")
def heartbeat(
    dataset_id: int, sample_id: int, request: Request,
    x_csrf_token: str = Header(default=""),
    db: Session = Depends(get_db),
):
    """Gia hạn khóa 10 phút khi trình duyệt còn kết nối."""
    context, dataset, error = request_context(request, db, dataset_id)
    if error is not None:
        return JSONResponse({"ok": False}, status_code=401 if context is None else 403)
    if not is_valid_csrf(context, x_csrf_token):
        return JSONResponse({"ok": False}, status_code=403)
    try:
        load_sample(db, dataset_id, sample_id)
    except LabelingError:
        return JSONResponse({"ok": False}, status_code=404)
    renewed = renew_lease(db, sample_id, context.session.id)
    return JSONResponse({"ok": renewed}, status_code=200 if renewed else 409)


@router.post("/{dataset_id}/release")
def release_sample(
    dataset_id: int, request: Request,
    sample_id: int = Form(...), csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    """Bỏ qua audio hiện tại, giữ nguyên nhãn và trạng thái."""
    context, dataset, error = request_context(request, db, dataset_id)
    if error is not None:
        return error
    rejected = protected_form(context, csrf_token)
    if rejected is not None:
        return rejected
    try:
        load_sample(db, dataset_id, sample_id)
        release_lease(db, sample_id, context.session.id)
    except LabelingError as exc:
        return redirect_page(dataset_id, error=str(exc))
    return redirect_page(dataset_id, message="Đã bỏ qua mẫu, không thay đổi nhãn.")


@router.post("/{dataset_id}/confirm")
def confirm(
    dataset_id: int, request: Request, sample_id: int = Form(...),
    version: int = Form(...), label: str = Form(default=""),
    no_speech: bool = Form(default=False), next_after_save: bool = Form(default=False),
    csrf_token: str = Form(...), db: Session = Depends(get_db),
):
    """Lưu vào SQLite, giữ nguyên CSV và tự chuyển mẫu khi được yêu cầu."""
    context, dataset, error = request_context(request, db, dataset_id)
    if error is not None:
        return error
    rejected = protected_form(context, csrf_token)
    if rejected is not None:
        return rejected
    try:
        sample = load_sample(db, dataset_id, sample_id)
        confirm_sample(db, sample, context.user.id, context.session.id, version, label, no_speech)
    except LabelingError as exc:
        db.rollback()
        db.expire_all()
        current = db.get(Sample, sample_id)
        if current is not None and current.dataset_id != dataset_id:
            current = None
        return labeling_page_response(
            request, db, context, dataset, current, error=str(exc), draft_label=label
        )
    if next_after_save:
        newly_claimed = claim_next(db, dataset_id, context.user.id, context.session.id)
        if newly_claimed is not None:
            return redirect_page(dataset_id, sample_id=newly_claimed.id, message="Đã lưu SQLite.")
    return redirect_page(dataset_id, message="Đã lưu vào SQLite. Chưa xuất CSV.")
