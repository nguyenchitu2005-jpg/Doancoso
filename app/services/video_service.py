from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile


class VideoService:
    """Placeholder service for upload and frame extraction logic."""

    def __init__(self, upload_dir: str | Path = "uploads") -> None:
        self.upload_dir = Path(upload_dir)
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.allowed_extensions = {".mp4", ".avi", ".mov", ".mkv"}

    def build_upload_path(self, filename: str) -> Path:
        return self.upload_dir / filename

    def _sanitize_name(self, filename: str) -> str:
        safe_name = Path(filename).name.replace(" ", "_")
        return "".join(char for char in safe_name if char.isalnum() or char in {"_", "-", "."})

    def _format_size(self, size_bytes: int) -> str:
        units = ["B", "KB", "MB", "GB"]
        size = float(size_bytes)
        unit = units[0]
        for unit in units:
            if size < 1024 or unit == units[-1]:
                break
            size /= 1024
        return f"{size:.1f} {unit}"

    async def save_upload(self, upload: UploadFile) -> dict:
        original_name = upload.filename or ""
        extension = Path(original_name).suffix.lower()
        if not original_name:
            raise ValueError("Vui lòng chọn một video để tải lên.")
        if extension not in self.allowed_extensions:
            raise ValueError("Định dạng không hợp lệ. Chỉ hỗ trợ .mp4, .avi, .mov, .mkv.")

        safe_name = self._sanitize_name(original_name)
        stored_name = f"{uuid4().hex[:8]}_{safe_name}"
        destination = self.build_upload_path(stored_name)

        total_size = 0
        with destination.open("wb") as output_file:
            while True:
                chunk = await upload.read(1024 * 1024)
                if not chunk:
                    break
                total_size += len(chunk)
                output_file.write(chunk)
        await upload.close()

        return {
            "original_filename": original_name,
            "stored_filename": stored_name,
            "path": str(destination),
            "size_bytes": total_size,
            "size_label": self._format_size(total_size),
        }

    def list_uploads(self, limit: int = 5) -> list[dict]:
        uploads = []
        for file_path in sorted(self.upload_dir.glob("*"), key=lambda path: path.stat().st_mtime, reverse=True)[:limit]:
            if file_path.is_file() and not file_path.name.startswith("."):
                uploads.append(
                    {
                        "filename": file_path.name,
                        "size_label": self._format_size(file_path.stat().st_size),
                    }
                )
        return uploads
