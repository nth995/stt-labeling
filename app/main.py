"""Điểm khởi động ứng dụng STT Labeling."""
import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.database.connection import initialize_database
from app.routes import admin, admin_datasets, auth, labeling, csv_sync
from app.services.activity_service import sync_disconnected_sessions
from app.services.csv_sync_service import sync_dirty_on_startup
from app.services.auth_service import validate_session_secret


LOGGER = logging.getLogger(__name__)


async def csv_disconnect_watcher() -> None:
    """Kiểm tra mỗi 30 giây các phiên gán nhãn đã mất tín hiệu."""
    while True:
        await asyncio.sleep(30)
        try:
            await asyncio.to_thread(sync_disconnected_sessions)
        except Exception:
            LOGGER.exception("Lỗi kiểm tra phiên mất kết nối để đồng bộ CSV")


@asynccontextmanager
async def lifespan(application: FastAPI):
    """Khởi tạo SQLite, đồng bộ bù sau lỗi và theo dõi phiên bị ngắt."""
    validate_session_secret()
    initialize_database()
    await asyncio.to_thread(sync_dirty_on_startup)
    watcher = asyncio.create_task(csv_disconnect_watcher())
    try:
        yield
    finally:
        watcher.cancel()
        with suppress(asyncio.CancelledError):
            await watcher


app = FastAPI(title="STT Labeling", lifespan=lifespan)
app.mount(
    "/static",
    StaticFiles(directory=Path(__file__).parent / "static"),
    name="static",
)
app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(admin_datasets.router)

app.include_router(labeling.router)

app.include_router(csv_sync.router)
