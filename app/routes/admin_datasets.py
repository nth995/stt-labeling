"""Trang Admin quản lý dataset và phân quyền sử dụng."""
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.database.models import Dataset, User, UserDataset, Sample, SampleLock
from app.routes.admin import admin_context
from app.services.auth_service import get_csrf_token, is_valid_csrf
from app.services.dataset_service import (
    discover_datasets,
    initialize_dataset_files,
    inspect_dataset,
    register_datasets,
)

router = APIRouter(prefix="/admin/datasets")
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "templates"))


def redirect_notice(message: str, error: bool = False):
    """Hiển thị phản hồi thao tác trên trang quản lý dataset."""
    query = urlencode({"error" if error else "message": message})
    return RedirectResponse(f"/admin/datasets?{query}", status_code=303)


def verify_admin_form(request: Request, db: Session, token: str):
    """Kiểm tra quyền Admin và token chống giả mạo biểu mẫu."""
    context, response = admin_context(request, db)
    if response is not None:
        return response
    if not is_valid_csrf(context, token):
        return HTMLResponse("Yêu cầu không hợp lệ.", status_code=403)
    return None


@router.get("", response_class=HTMLResponse)
def dataset_admin_page(request: Request, db: Session = Depends(get_db)):
    """Liệt kê dataset đã đăng ký, tình trạng CSV và tài khoản được cấp quyền."""
    context, response = admin_context(request, db)
    if response is not None:
        return response
    datasets = db.scalars(select(Dataset).order_by(Dataset.folder_name)).all()
    annotators = db.scalars(
        select(User).where(User.role == "annotator").order_by(User.username)
    ).all()
    assigned = set(db.execute(select(UserDataset.user_id, UserDataset.dataset_id)).all())
    folder_names = set(discover_datasets())
    registered_names = {dataset.folder_name for dataset in datasets}
    listings = []
    for dataset in datasets:
        if dataset.folder_name in folder_names:
            details = inspect_dataset(dataset.folder_name)
            missing = False
        else:
            details = None
            missing = True
        listings.append({"record": dataset, "details": details, "missing": missing})
    return templates.TemplateResponse(
        request=request,
        name="admin_datasets.html",
        context={
            "user": context.user,
            "csrf_token": get_csrf_token(context),
            "datasets": listings,
            "annotators": annotators,
            "assigned": assigned,
            "unregistered": sorted(folder_names - registered_names),
            "message": request.query_params.get("message"),
            "error": request.query_params.get("error"),
        },
    )


@router.post("/scan")
def scan_dataset_folders(
    request: Request,
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    """Cập nhật danh sách thư mục dataset trong SQLite."""
    response = verify_admin_form(request, db, csrf_token)
    if response is not None:
        return response
    amount = register_datasets(db)
    return redirect_notice(f"Đã đăng ký thêm {amount} dataset. Không thay đổi file audio hoặc nhãn.")


@router.post("/{dataset_id}/initialize")
def initialize_csv_files(
    dataset_id: int,
    request: Request,
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    """Khởi tạo hai CSV nếu thiếu, không ghi đè dữ liệu cũ."""
    response = verify_admin_form(request, db, csrf_token)
    if response is not None:
        return response
    dataset = db.get(Dataset, dataset_id)
    if dataset is None:
        return redirect_notice("Không tìm thấy dataset.", error=True)
    try:
        message = initialize_dataset_files(dataset.folder_name)
    except (ValueError, OSError, UnicodeError) as error:
        return redirect_notice(str(error), error=True)
    return redirect_notice(f"{dataset.folder_name}: {message}")


@router.post("/{dataset_id}/permissions")
def update_dataset_permissions(
    dataset_id: int,
    request: Request,
    user_ids: list[int] = Form(default=[]),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    """Cập nhật danh sách Annotator được phép truy cập dataset."""
    response = verify_admin_form(request, db, csrf_token)
    if response is not None:
        return response
    dataset = db.get(Dataset, dataset_id)
    if dataset is None:
        return redirect_notice("Không tìm thấy dataset.", error=True)
    try:
        details = inspect_dataset(dataset.folder_name)
    except ValueError as error:
        return redirect_notice(str(error), error=True)
    if not details.ready:
        return redirect_notice("Hãy khởi tạo CSV và sửa các lỗi dữ liệu trước khi cấp quyền.", error=True)

    requested = set(user_ids)
    allowed = set(db.scalars(select(User.id).where(User.role == "annotator")).all())
    if requested - allowed:
        return redirect_notice("Có tài khoản không phải Annotator hoặc không tồn tại.", error=True)

    current = set(db.scalars(
        select(UserDataset.user_id).where(UserDataset.dataset_id == dataset.id)
    ).all())
    for user_id in requested - current:
        db.add(UserDataset(user_id=user_id, dataset_id=dataset.id))
    for user_id in current - requested:
        relation = db.get(UserDataset, (user_id, dataset.id))
        if relation is not None:
            db.delete(relation)
    # Thu hồi luôn những khóa audio của người vừa mất quyền dataset.
    if current - requested:
        sample_ids = select(Sample.id).where(Sample.dataset_id == dataset.id)
        db.execute(delete(SampleLock).where(
            SampleLock.user_id.in_(current - requested), SampleLock.sample_id.in_(sample_ids)
        ))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return redirect_notice("Không thể cập nhật phân quyền, vui lòng thử lại.", error=True)
    return redirect_notice(f"Đã cập nhật quyền truy cập dataset {dataset.folder_name}.")
