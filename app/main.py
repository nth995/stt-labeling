
import csv
from pathlib import Path

from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse

app = FastAPI(title="STT Labeling Demo")

DATA_DIR = Path("data")
LABEL_FILE = DATA_DIR / "label.csv"


@app.get("/", response_class=HTMLResponse)
def show_labeling_page():
    """Hiển thị giao diện chỉnh sửa nhãn thử nghiệm."""
    return """
    <html lang="vi">
    <meta charset="utf-8">
    <h2>STT Labeling Tool</h2>

    <form action="/save" method="post">
        <p>Nhãn gốc:</p>
        <textarea name="original" rows="3" cols="60">Tôi muốn chuyển tiền</textarea>

        <p>Nhãn chỉnh sửa:</p>
        <textarea name="corrected" rows="3" cols="60" required></textarea>

        <p><button type="submit">Lưu nhãn</button></p>
    </form>
    </html>
    """


@app.post("/save", response_class=HTMLResponse)
def save_label(
    original: str = Form(...),
    corrected: str = Form(...),
):
    """Lưu nhãn gốc và nhãn chỉnh sửa vào CSV."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    is_new_file = not LABEL_FILE.exists()

    with LABEL_FILE.open("a", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)

        if is_new_file:
            writer.writerow(["original", "corrected"])

        writer.writerow([original, corrected])

    return '<h3>Đã lưu nhãn thành công!</h3><a href="/">Quay lại</a>'
