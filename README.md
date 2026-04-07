# AI Proctoring FastAPI Scaffold

Khung dự án này chuyển giao diện hiện tại sang mô hình `FastAPI + Jinja2 + static assets`, để bạn có thể gắn backend Python cho YOLO, upload video và dashboard mà không phải làm lại frontend.

## Cách chạy

1. Tạo môi trường ảo:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

2. Cài thư viện:

```powershell
pip install -r requirements.txt
```

3. Chạy server:

```powershell
python run.py
```

Hoặc:

```powershell
uvicorn app.main:app --reload
```

4. Mở trình duyệt:

- `http://127.0.0.1:8000/`
- `http://127.0.0.1:8000/api/dashboard`
- `POST http://127.0.0.1:8000/review/upload`
- `http://127.0.0.1:8000/docs`

## Cấu trúc thư mục

```text
app/
  main.py
  routes/
    web.py
    api.py
  services/
    dashboard_service.py
    detection_service.py
    video_service.py
  schemas/
  templates/
    base.html
    dashboard.html
  static/
    css/
      styles.css
    js/
      dashboard.js
data/
uploads/
results/
weights/
run.py
requirements.txt


## File chính

- `app/main.py`: khởi tạo FastAPI, mount static và include router.
- `app/routes/web.py`: render dashboard bằng Jinja2.
- `app/routes/api.py`: trả dữ liệu JSON mẫu cho dashboard.
- `app/services/dashboard_service.py`: nơi đang chứa dữ liệu giả để cấp cho giao diện.
- `app/services/detection_service.py`: chỗ để nối YOLO inference sau này.
- `app/services/video_service.py`: chỗ để xử lý upload/video path.
- `app/templates/dashboard.html`: giao diện chính dùng cho backend.
- `app/static/js/dashboard.js`: xử lý chuyển tab, render bảng thí sinh, slider.
- `app/static/css/styles.css`: stylesheet của giao diện.

## Bước tiếp theo nên làm

1. Nối form upload với `FastAPI UploadFile`.
2. Gọi `DetectionService` để chạy YOLO trên video/webcam.
3. Lưu kết quả vào SQLite hoặc MySQL.
4. Thay dữ liệu giả trong `dashboard_service.py` bằng dữ liệu thật từ DB.

## Upload hiện có

- Form upload trong tab `Hậu kiểm` đã được nối với route `POST /review/upload`.
- File được lưu vào thư mục `uploads/`.
- Sau khi upload, backend sẽ gọi `DetectionService`.
- Kết quả phân tích được lưu thành file JSON trong thư mục `results/`.
- Giao diện tự quay lại tab `Hậu kiểm` và hiển thị trạng thái upload cùng trạng thái phân tích.

## YOLO review hiện có

- `DetectionService` đang hỗ trợ hậu kiểm video bằng YOLO theo hướng đơn giản:
  - phát hiện `cell phone`
  - phát hiện `nhiều người trong khung hình`
- Dịch vụ lấy mẫu frame theo chu kỳ rồi chạy YOLO, sau đó ghi sự kiện vào `results/<video>.json`.
- Nếu máy chưa có `ultralytics`, `opencv-python-headless` hoặc chưa sẵn weight phù hợp, hệ thống vẫn chạy và trả trạng thái `skipped` thay vì làm app lỗi.

## Chuẩn bị model

- Cài dependency mới:

```powershell
pip install -r requirements.txt
```

- Bạn có thể đặt file weight vào thư mục `weights/`, ví dụ:

```text
weights/yolo11n.pt
```

- Mặc định service đang dùng `yolo11n.pt`. Nếu không có file trong `weights/`, Ultralytics có thể tự tải model khi môi trường hỗ trợ.

## Ghi chú

- Giao diện sử dụng thật nằm trong `app/templates` và `app/static`.
