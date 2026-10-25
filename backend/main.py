from fastapi import FastAPI, UploadFile, File, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response
from typing import List
import google.generativeai as genai
import fitz  # PyMuPDF
import asyncio
import json
import logging
import os
from dotenv import load_dotenv
from thoi_gian import now_vn
import pathlib

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("benh_an")

import db
import auth
import email_service
from auth import get_current_user
from email_service import EmailError
from excel_export import build_excel, excel_filename, XLSX_MIME
from dao_van import find_pairs
import admin

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
model = genai.GenerativeModel("gemini-2.5-flash")

app = FastAPI(title="Chấm Bệnh Án Y Khoa")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],  # để frontend (Netlify) đọc được tên file khi tải về
)

db.init_db()
log.info("Cách gửi email: %s", email_service.provider())

# ── Tài khoản: đăng ký, xác nhận email, đăng nhập, đổi/quên mật khẩu (xem auth.py) ──
app.include_router(auth.router)
# ── Quản trị: người dùng, lịch sử toàn hệ thống, đạo văn chéo, sao lưu (xem admin.py) ──
app.include_router(admin.router)

RUBRIC = """
Bạn là giảng viên y khoa chấm bệnh án sinh viên. Hãy chấm điểm theo rubric sau (thang 10 điểm, điểm lẻ 0.25):

=== PHẦN A: CÁC ĐỀ MỤC (tối đa 7.0 điểm) ===

1. Hành chính (tối đa 0.25đ): Đủ 9 mục: Họ tên, Tuổi, Giới, Nghề nghiệp, Địa chỉ, Ngày vào viện, Ngày làm bệnh án, Giường/Phòng, Mã bệnh nhân.
   - 0.25: Đủ 9 mục | 0: Thiếu từ 1 mục trở lên

2. Lý do vào viện (tối đa 0.25đ): Nêu đúng và đủ lý do chính vào viện.
   - 0.25: Đúng, rõ ràng | 0: Sai, thiếu hoặc không có

3. Bệnh sử (tối đa 0.75đ): Mô tả đủ thời gian, hoàn cảnh, đặc điểm, diễn biến, triệu chứng kèm theo, điều trị trước đó, triệu chứng hiện tại.
   - 0.75: Đủ tất cả | 0.5: Thiếu 1 yếu tố | 0.25: Thiếu 2-3 yếu tố | 0: Qua loa hoặc không có

4. Tiền sử bệnh (tối đa 0.25đ): Hút thuốc (số bao-năm), bệnh mạn tính, dị ứng, dịch tễ, tiền sử gia đình.
   - 0.25: Đủ ≥3 mục | 0: Dưới 3 mục hoặc không có

5. Khám bệnh (tối đa 1.0đ): Khám vào viện + Khám hiện tại đủ: toàn thân, hô hấp (nhìn-sờ-gõ-nghe), tim mạch, tiêu hoá, thận-tiết niệu, cơ xương khớp, cơ quan khác.
   - 1.0: Đủ chi tiết | 0.75: Thiếu 1-2 hệ | 0.5: Thiếu ≥3 hệ | 0.25: Sơ sài | 0: Không có

6. Tóm tắt bệnh án (tối đa 0.75đ): Đúng chuẩn: giới-tuổi-lý do-số ngày bệnh, hội chứng dương tính, âm tính, tiền sử.
   - 0.75: Đủ cấu trúc | 0.5: Thiếu âm tính/tiền sử | 0.25: Chưa đầy đủ | 0: Rất sơ sài hoặc không có

7. Chẩn đoán sơ bộ (tối đa 0.75đ): Phù hợp với bệnh sử và tóm tắt bệnh án.
   - 0.75: Đúng, có căn cứ | 0.5: Đúng hướng, chưa đầy đủ | 0.25: Mơ hồ | 0: Sai

8. Đề xuất cận lâm sàng (tối đa 0.75đ): Đề xuất đúng và nêu lý do cho từng xét nghiệm.
   - 0.75: Đầy đủ, có lý do | 0.5: Đủ nhưng thiếu một số lý do | 0.25: Thiếu xét nghiệm quan trọng hoặc không có lý do | 0: Sai/không có

9. Kết quả cận lâm sàng (tối đa 0.75đ): Trình bày đúng thứ tự, có nhận định bình thường/bất thường.
   - 0.75: Đầy đủ, nhận định chính xác | 0.5: Đủ, nhận định chưa đầy đủ | 0.25: Thiếu nhiều hoặc thiếu nhận định | 0: Không có

10. Chẩn đoán xác định (tối đa 0.75đ): Đúng và có biện luận dựa trên lâm sàng + cận lâm sàng.
    - 0.75: Đúng, biện luận đầy đủ | 0.5: Đúng, biện luận chưa đủ | 0.25: Sai một phần hoặc thiếu biện luận | 0: Sai

11. Điều trị (tối đa 0.75đ): Nguyên tắc điều trị, kế hoạch (thuốc + không thuốc + phác đồ), tư vấn sau ra viện.
    - 0.75: Đầy đủ 3 phần | 0.5: Đủ nguyên tắc và phác đồ, thiếu tư vấn | 0.25: Chỉ nêu được một phần | 0: Không có

=== PHẦN B: TƯ DUY LÂM SÀNG (tối đa 3.0 điểm) ===

12. Tư duy lâm sàng mạch lạc (tối đa 3.0đ): Đánh giá tính thống nhất và logic trong toàn bộ bệnh án: bệnh sử → khám bệnh → tóm tắt → chẩn đoán sơ bộ → cận lâm sàng → chẩn đoán xác định → điều trị. Các phần có ăn khớp, bổ sung cho nhau không? Có mâu thuẫn nội tại không? Biện luận có được xây dựng nhất quán từ dữ liệu lâm sàng không?
    - 3.0: Tư duy rõ ràng, mạch lạc, nhất quán xuyên suốt; biện luận logic từ triệu chứng → chẩn đoán → điều trị
    - 2.25: Tư duy nhất quán ở hầu hết các phần, có vài điểm chưa ăn khớp nhưng không ảnh hưởng kết luận
    - 1.5: Có sự thống nhất cơ bản nhưng xuất hiện mâu thuẫn ở một số phần quan trọng
    - 0.75: Tư duy rời rạc, các phần thiếu liên kết, mâu thuẫn rõ ràng
    - 0: Không có tư duy lâm sàng rõ ràng, các phần không ăn khớp hoặc mâu thuẫn nhau

Ngoài chấm điểm, hãy trích xuất thông tin hành chính của bệnh nhân và liệt kê cụ thể các phần sinh viên cần sửa.

Hãy trả về JSON với định dạng chính xác sau (không thêm bất kỳ văn bản nào khác):
{
  "tong_diem": <số thực, tổng của tất cả 12 mục, tối đa 10.0>,
  "xep_loai": "<Xuất sắc/Giỏi/Khá/Trung bình/Yếu>",
  "benh_nhan": {
    "ho_ten": "<họ tên bệnh nhân hoặc viết tắt nếu có>",
    "tuoi": "<tuổi>",
    "gioi": "<giới tính>",
    "ma_benh_nhan": "<mã bệnh nhân nếu có>"
  },
  "chi_tiet": [
    {
      "stt": 1,
      "ten_muc": "Hành chính",
      "diem_toi_da": 0.25,
      "diem_dat": <số>,
      "nhan_xet": "<nhận xét ngắn gọn>"
    }
  ],
  "cac_phan_can_sua": [
    "<mô tả cụ thể phần cần sửa 1>",
    "<mô tả cụ thể phần cần sửa 2>"
  ],
  "nhan_xet_chung": "<nhận xét tổng thể về bệnh án>"
}
"""

def extract_text_from_pdf(file_bytes: bytes) -> str:
    with fitz.open(stream=file_bytes, filetype="pdf") as doc:
        text = ""
        for page in doc:
            text += page.get_text()
    return text.strip()

def check_plagiarism(records: list) -> list:
    """So sánh các cặp bệnh án để phát hiện trùng lặp (xem dao_van.py)"""
    return find_pairs(records)

@app.post("/cham-nhieu")
async def cham_nhieu(files: List[UploadFile] = File(...), user=Depends(get_current_user)):
    results = []
    errors = []

    for file in files:
        if not file.filename.lower().endswith(".pdf"):
            errors.append({"file": file.filename, "loi": "Không phải file PDF"})
            continue
        try:
            content = await file.read()
            text = await asyncio.to_thread(extract_text_from_pdf, content)
            if not text:
                errors.append({"file": file.filename, "loi": "Không đọc được nội dung PDF"})
                continue

            prompt = f"{RUBRIC}\n\nNỘI DUNG BỆNH ÁN:\n{text}"
            # Chạy trong luồng riêng để khi một người đang chấm, người khác vẫn dùng app bình thường
            response = await asyncio.to_thread(model.generate_content, prompt)
            raw = response.text.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            result = json.loads(raw.strip())
            result["ten_file"] = file.filename
            result["thoi_gian"] = now_vn().strftime("%d/%m/%Y %H:%M")
            results.append(result)
        except Exception as e:
            errors.append({"file": file.filename, "loi": str(e)})

    # Lưu vào lịch sử của người dùng (mỗi kết quả được gán "id")
    if results:
        db.add_records(user["id"], results)

    # Kiểm tra đạo văn trong lô này
    plagiarism = check_plagiarism(results) if len(results) > 1 else []

    return {
        "results": results,
        "errors": errors,
        "plagiarism": plagiarism,
        "tong_so": len(results),
        "loi_so": len(errors)
    }

@app.get("/lich-su")
def get_lich_su(user=Depends(get_current_user)):
    return db.list_records(user["id"])

@app.delete("/lich-su")
def xoa_lich_su(user=Depends(get_current_user)):
    db.delete_records(user["id"])
    return {"message": "Đã xoá toàn bộ lịch sử của bạn"}

@app.post("/kiem-tra-dao-van")
def kiem_tra_dao_van(user=Depends(get_current_user)):
    """Kiểm tra đạo văn trong toàn bộ lịch sử của người dùng"""
    history = db.list_records(user["id"])
    if len(history) < 2:
        return {"pairs": [], "message": "Cần ít nhất 2 bệnh án để so sánh"}
    pairs = check_plagiarism(history)
    return {"pairs": pairs, "tong_cap": len(pairs)}

@app.post("/xuat-excel-nhieu")
def xuat_excel_nhieu(data: dict, user=Depends(get_current_user)):
    results = data.get("results", [])
    if not results:
        raise HTTPException(status_code=400, detail="Không có dữ liệu")
    content = build_excel(results, data.get("plagiarism", []))
    fname = excel_filename()
    return Response(content, media_type=XLSX_MIME,
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})

@app.post("/gui-email-ket-qua")
def gui_email_ket_qua(data: dict, user=Depends(get_current_user)):
    """Gửi file Excel tổng hợp của một lần chấm tới email của người dùng.
    Body: {"record_ids": [...], "plagiarism": [...]} – record_ids là "id" trả về từ /cham-nhieu."""
    ids = data.get("record_ids") or []
    if not isinstance(ids, list) or not ids:
        raise HTTPException(400, "Không có kết quả nào để gửi.")
    results = db.get_records(user["id"], ids[:500])
    if not results:
        raise HTTPException(404, "Không tìm thấy kết quả chấm.")
    plagiarism = data.get("plagiarism") or []
    if not isinstance(plagiarism, list):
        plagiarism = []
    when = now_vn().strftime("%H:%M %d/%m/%Y")
    try:
        email_service.send_results_email(
            user["email"], user["ho_ten"], results, plagiarism,
            build_excel(results, plagiarism), excel_filename(), when,
        )
    except EmailError as e:
        raise HTTPException(502, f"Không gửi được email: {e}")
    return {"message": f"Đã gửi file Excel kết quả tới {user['email']}", "email": user["email"]}

STATIC_DIR = pathlib.Path(__file__).parent / "static"

@app.get("/", response_class=HTMLResponse)
def serve_frontend():
    index = STATIC_DIR / "index.html"
    if index.exists():
        return index.read_text(encoding="utf-8")
    return HTMLResponse("<h1>Frontend not found</h1>", status_code=404)

@app.get("/health")
def health():
    return {"status": "ok", "email": email_service.provider()}
