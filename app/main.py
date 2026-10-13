"""Điểm khởi động ứng dụng STT Labeling."""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.database.connection import initialize_database
from app.routes import admin, auth
from app.services.auth_service import validate_session_secret


@asynccontextmanager
async def lifespan(application: FastAPI):
    """Chuẩn bị cơ sở dữ liệu trước khi nhận yêu cầu."""
    validate_session_secret()
    initialize_database()
    yield


app = FastAPI(title="STT Labeling", lifespan=lifespan)
app.mount(
    "/static",
    StaticFiles(directory=Path(__file__).parent / "static"),
    name="static",
)
app.include_router(auth.router)
app.include_router(admin.router)
