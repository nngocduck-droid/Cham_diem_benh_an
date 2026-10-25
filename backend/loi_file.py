"""
Phát hiện và mô tả lỗi của từng file khi chấm (để báo rõ cho người dùng và ghi vào Excel/email).

Mỗi lỗi gồm:
  loi   – mô tả lỗi, dễ hiểu
  goi_y – cách khắc phục
"""
import json
import re

import fitz  # PyMuPDF

MAX_FILE_MB = 30
MIN_TEXT_CHARS = 50   # ít hơn số ký tự này → coi như PDF không có chữ (bản scan/ảnh chụp)


class FileLoi(Exception):
    def __init__(self, loi: str, goi_y: str = ""):
        super().__init__(loi)
        self.loi = loi
        self.goi_y = goi_y

    def to_dict(self, ten_file: str) -> dict:
        return {"file": ten_file, "loi": self.loi, "goi_y": self.goi_y}


# ── Kiểm tra file & đọc PDF ──

def kiem_tra_file(ten_file: str, content: bytes):
    if not (ten_file or "").lower().endswith(".pdf"):
        raise FileLoi("Không phải file PDF",
                      "Xuất bệnh án sang PDF (File → Lưu thành / Xuất → PDF) rồi tải lên lại.")
    if not content:
        raise FileLoi("File rỗng (0 KB)", "Kiểm tra lại file gốc rồi tải lên lại.")
    mb = len(content) / 1024 / 1024
    if mb > MAX_FILE_MB:
        raise FileLoi(f"File quá lớn ({mb:.1f} MB, tối đa {MAX_FILE_MB} MB)",
                      "Giảm dung lượng PDF (nén ảnh hoặc xuất lại từ Word) rồi tải lên lại.")


def doc_pdf(content: bytes) -> str:
    try:
        doc = fitz.open(stream=content, filetype="pdf")
    except Exception:
        raise FileLoi("File PDF bị hỏng hoặc không đúng định dạng PDF",
                      "Mở thử file trên máy; nếu không mở được, xuất lại PDF từ file gốc.")
    try:
        if doc.needs_pass:
            raise FileLoi("File PDF được đặt mật khẩu",
                          "Bỏ mật khẩu (mở file rồi in/lưu ra một PDF mới) rồi tải lên lại.")
        if doc.page_count == 0:
            raise FileLoi("File PDF không có trang nào", "Xuất lại PDF từ file gốc.")
        try:
            text = "".join(page.get_text() for page in doc).strip()
        except Exception:
            raise FileLoi("File PDF bị hỏng, không đọc được nội dung",
                          "Xuất lại PDF từ file gốc rồi tải lên lại.")
    finally:
        doc.close()
    so_ky_tu = len(re.sub(r"\s+", "", text))
    if so_ky_tu < MIN_TEXT_CHARS:
        raise FileLoi(
            f"PDF không có chữ đọc được (chỉ {so_ky_tu} ký tự) – có thể là bản scan hoặc ảnh chụp",
            "Xuất PDF trực tiếp từ Word/Google Docs thay vì scan hay chụp ảnh bệnh án.")
    return text


# ── Lỗi khi gọi AI (Gemini) ──

def loi_ai(e: Exception) -> FileLoi:
    name = type(e).__name__
    msg = str(e)
    low = msg.lower()
    if name in ("ResourceExhausted", "TooManyRequests") or "429" in msg or "quota" in low:
        return FileLoi("Hết hạn mức gọi AI (Gemini) – quá nhiều yêu cầu trong thời gian ngắn",
                       "Đợi vài phút rồi chấm lại file này.")
    if name in ("PermissionDenied", "Unauthenticated") or "api key" in low or "api_key" in low:
        return FileLoi("Lỗi cấu hình AI (khoá Gemini không hợp lệ hoặc hết hạn)",
                       "Báo quản trị viên kiểm tra biến GEMINI_API_KEY trên Railway.")
    if name in ("ServiceUnavailable", "DeadlineExceeded", "InternalServerError", "GatewayTimeout",
                "RetryError", "TimeoutError") or "503" in msg or "timed out" in low or "timeout" in low:
        return FileLoi("Máy chủ AI đang quá tải hoặc phản hồi quá chậm",
                       "Chấm lại file này sau ít phút.")
    if name in ("BlockedPromptException", "StopCandidateException") or "safety" in low or "blocked" in low:
        return FileLoi("AI từ chối xử lý nội dung file này",
                       "Kiểm tra nội dung file; nếu bình thường, thử chấm lại.")
    if name == "InvalidArgument" or "400" in msg:
        return FileLoi("AI không xử lý được nội dung file (có thể quá dài hoặc chứa ký tự lạ)",
                       "Kiểm tra lại file; thử xuất lại PDF rồi chấm lại.")
    return FileLoi(f"Lỗi khi gọi AI: {msg[:200]}",
                   "Chấm lại file này; nếu lỗi lặp lại, báo quản trị viên.")


def lay_text_phan_hoi(response) -> str:
    try:
        return response.text or ""
    except ValueError:
        # response.text báo ValueError khi AI không trả nội dung (bị chặn vì an toàn, v.v.)
        raise FileLoi("AI từ chối xử lý hoặc không trả kết quả cho file này",
                      "Kiểm tra nội dung file; nếu bình thường, thử chấm lại.")


# ── Kiểm tra kết quả AI trả về ──

def _so(v):
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None


def xep_loai_tu_diem(d: float) -> str:
    if d >= 9:
        return "Xuất sắc"
    if d >= 8:
        return "Giỏi"
    if d >= 6.5:
        return "Khá"
    if d >= 5:
        return "Trung bình"
    return "Yếu"


def phan_tich_ket_qua(raw: str) -> dict:
    raw = (raw or "").strip()
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1] if len(parts) > 1 else raw
        if raw.lower().startswith("json"):
            raw = raw[4:]
    raw = raw.strip()
    result = None
    try:
        result = json.loads(raw)
    except Exception:
        a, b = raw.find("{"), raw.rfind("}")
        if a != -1 and b > a:
            try:
                result = json.loads(raw[a:b + 1])
            except Exception:
                result = None
    if not isinstance(result, dict):
        raise FileLoi("AI trả kết quả sai định dạng",
                      "Chấm lại file này (lỗi này thường chỉ xảy ra ngẫu nhiên).")

    chi_tiet = result.get("chi_tiet")
    if not isinstance(chi_tiet, list) or not chi_tiet:
        raise FileLoi("AI trả kết quả thiếu điểm chi tiết từng mục",
                      "Chấm lại file này (lỗi này thường chỉ xảy ra ngẫu nhiên).")
    for item in chi_tiet:
        if isinstance(item, dict):
            item["diem_dat"] = _so(item.get("diem_dat")) or 0.0
            item["diem_toi_da"] = _so(item.get("diem_toi_da")) or 0.0
    tong = _so(result.get("tong_diem"))
    if tong is None:
        tong = round(sum(i.get("diem_dat", 0) for i in chi_tiet if isinstance(i, dict)), 2)
    result["tong_diem"] = tong
    if not result.get("xep_loai"):
        result["xep_loai"] = xep_loai_tu_diem(tong)
    if not isinstance(result.get("benh_nhan"), dict):
        result["benh_nhan"] = {}
    return result


# ── Làm sạch danh sách lỗi do trình duyệt gửi lên (để ghi vào Excel/email) ──

def lam_sach_loi(errors) -> list:
    out = []
    if not isinstance(errors, list):
        return out
    for e in errors[:50]:
        if not isinstance(e, dict):
            continue
        out.append({
            "file": str(e.get("file") or "")[:255],
            "loi": str(e.get("loi") or "Lỗi không xác định")[:400],
            "goi_y": str(e.get("goi_y") or "")[:400],
        })
    return out
