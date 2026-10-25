"""
Chức năng quản trị (chỉ admin):
  - Quản lý người dùng: xem danh sách, khoá/mở khoá, xác nhận email thay, gửi link đặt lại mật khẩu,
    cấp/thu quyền admin, xoá tài khoản (giữ hoặc xoá lịch sử chấm).
  - Xem lịch sử chấm của mọi người (lọc theo người chấm, ngày, điểm), xuất Excel toàn hệ thống.
  - Kiểm tra đạo văn chéo giữa các người chấm.
  - Tải bản sao lưu CSDL.
  - Nhật ký quản trị: mọi thao tác trên đều được ghi lại (kể cả việc xem kết quả chấm của người khác,
    vì kết quả có chứa thông tin bệnh nhân).

Quy tắc phân quyền:
  - Admin gốc = email trong ADMIN_EMAILS: luôn là admin, không thể bị khoá/xoá/thu quyền trong app.
  - Chỉ admin gốc mới được cấp/thu quyền admin và khoá/xoá tài khoản của admin khác.
  - Không ai tự khoá/tự xoá/tự thu quyền của chính mình.
"""
import json
import os
import tempfile
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response

import db
from auth import get_admin_user, is_admin, is_root_admin, send_account_link, _throttle, _unthrottle
from dao_van import find_pairs
from email_service import EmailError
from excel_export import XLSX_MIME, admin_excel_filename, build_admin_excel
from thoi_gian import now_vn

router = APIRouter(prefix="/admin")

MAX_LIST = 2000           # số dòng tối đa trả về cho bảng lịch sử
MAX_SCAN = 1500           # số bệnh án tối đa cho một lần quét đạo văn chéo (~1–2 phút)
MAX_PAIRS = 500


# ── Người dùng ──

def _user_status(u) -> str:
    if u["is_locked"]:
        return "bi_khoa"
    if not u["is_verified"]:
        return "chua_xac_nhan"
    return "hoat_dong"


def _admin_user_dict(u, me) -> dict:
    return {
        "id": u["id"],
        "email": u["email"],
        "ho_ten": u["ho_ten"],
        "trang_thai": _user_status(u),
        "la_admin": is_admin(u),
        "la_admin_goc": is_root_admin(u),
        "la_ban_than": u["id"] == me["id"],
        "so_benh_an": u["so_benh_an"] if "so_benh_an" in u.keys() else None,
        "lan_cham_gan_nhat": u["lan_cham_gan_nhat"] if "lan_cham_gan_nhat" in u.keys() else None,
        "created_at": u["created_at"],
        "last_login_at": u["last_login_at"],
        "locked_at": u["locked_at"],
    }


def _get_target(uid: int):
    with db.get_db() as conn:
        u = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not u:
        raise HTTPException(404, "Không tìm thấy tài khoản.")
    return u


def _label(u) -> str:
    return f"{u['ho_ten']} <{u['email']}>"


def _check_can_manage(admin, target, action: str):
    if target["id"] == admin["id"]:
        raise HTTPException(400, "Bạn không thể thực hiện thao tác này với chính tài khoản của mình.")
    if is_root_admin(target):
        raise HTTPException(403, "Đây là admin gốc (khai báo trong ADMIN_EMAILS trên server), không thể "
                                 f"{action} trong app.")
    if is_admin(target) and not is_root_admin(admin):
        raise HTTPException(403, f"Chỉ admin gốc mới được {action} tài khoản admin khác.")


@router.get("/tong-quan")
def tong_quan(admin=Depends(get_admin_user)):
    since = (now_vn().replace(hour=0, minute=0, second=0)).strftime("%Y-%m-%d %H:%M:%S")
    with db.get_db() as conn:
        users = conn.execute("SELECT * FROM users").fetchall()
        total_records = conn.execute("SELECT COUNT(*) FROM grading_records").fetchone()[0]
        today = conn.execute("SELECT COUNT(*) FROM grading_records WHERE created_at>=?", (since,)).fetchone()[0]
    st = [_user_status(u) for u in users]
    return {
        "tong_nguoi_dung": len(users),
        "hoat_dong": st.count("hoat_dong"),
        "chua_xac_nhan": st.count("chua_xac_nhan"),
        "bi_khoa": st.count("bi_khoa"),
        "so_admin": sum(1 for u in users if is_admin(u)),
        "tong_benh_an": total_records,
        "benh_an_hom_nay": today,
    }


@router.get("/nguoi-dung")
def danh_sach_nguoi_dung(admin=Depends(get_admin_user)):
    with db.get_db() as conn:
        rows = conn.execute("""
            SELECT u.*, COUNT(r.id) AS so_benh_an, MAX(r.created_at) AS lan_cham_gan_nhat
            FROM users u LEFT JOIN grading_records r ON r.user_id = u.id
            GROUP BY u.id ORDER BY u.created_at DESC
        """).fetchall()
    return [_admin_user_dict(u, admin) for u in rows]


@router.post("/nguoi-dung/{uid}/khoa")
def khoa_tai_khoan(uid: int, data: dict, admin=Depends(get_admin_user)):
    khoa = bool(data.get("khoa", True))
    target = _get_target(uid)
    _check_can_manage(admin, target, "khoá" if khoa else "mở khoá")
    with db.get_db() as conn:
        conn.execute("UPDATE users SET is_locked=?, locked_at=? WHERE id=?",
                     (1 if khoa else 0, db.now_str() if khoa else None, uid))
    db.log_admin(admin, "khoa" if khoa else "mo_khoa", _label(target))
    return {"message": ("Đã khoá tài khoản " if khoa else "Đã mở khoá tài khoản ") + target["email"]}


@router.post("/nguoi-dung/{uid}/xac-nhan")
def xac_nhan_thay(uid: int, admin=Depends(get_admin_user)):
    target = _get_target(uid)
    if target["is_verified"]:
        raise HTTPException(400, "Tài khoản này đã xác nhận email.")
    with db.get_db() as conn:
        conn.execute("UPDATE users SET is_verified=1, verified_at=? WHERE id=?", (db.now_str(), uid))
    db.log_admin(admin, "xac_nhan_email", _label(target))
    return {"message": f"Đã xác nhận email cho {target['email']}. Người dùng có thể đăng nhập ngay."}


@router.post("/nguoi-dung/{uid}/gui-link")
def gui_link(uid: int, request: Request, admin=Depends(get_admin_user)):
    target = _get_target(uid)
    if target["is_locked"]:
        raise HTTPException(400, "Tài khoản đang bị khoá. Hãy mở khoá trước khi gửi link.")
    _throttle("admin-link", target["email"])
    try:
        kind = send_account_link(request, target)
    except EmailError as e:
        _unthrottle("admin-link", target["email"])
        raise HTTPException(502, f"Không gửi được email: {e}")
    if kind == "reset":
        db.log_admin(admin, "gui_link_dat_lai", _label(target))
        return {"message": f"Đã gửi link đặt lại mật khẩu tới {target['email']} (hiệu lực 60 phút)."}
    db.log_admin(admin, "gui_link_xac_nhan", _label(target))
    return {"message": f"Tài khoản chưa xác nhận – đã gửi lại email xác nhận tới {target['email']}."}


@router.post("/nguoi-dung/{uid}/quyen-admin")
def quyen_admin(uid: int, data: dict, admin=Depends(get_admin_user)):
    cap = bool(data.get("admin", True))
    if not is_root_admin(admin):
        raise HTTPException(403, "Chỉ admin gốc mới được cấp hoặc thu quyền admin.")
    target = _get_target(uid)
    _check_can_manage(admin, target, "cấp quyền" if cap else "thu quyền admin của")
    if cap and (not target["is_verified"] or target["is_locked"]):
        raise HTTPException(400, "Chỉ cấp quyền admin cho tài khoản đã xác nhận email và không bị khoá.")
    with db.get_db() as conn:
        conn.execute("UPDATE users SET is_admin=? WHERE id=?", (1 if cap else 0, uid))
    db.log_admin(admin, "cap_quyen_admin" if cap else "thu_quyen_admin", _label(target))
    return {"message": ("Đã cấp quyền admin cho " if cap else "Đã thu quyền admin của ") + target["email"]}


@router.delete("/nguoi-dung/{uid}")
def xoa_tai_khoan(uid: int, giu_lich_su: bool = True, admin=Depends(get_admin_user)):
    target = _get_target(uid)
    _check_can_manage(admin, target, "xoá")
    with db.get_db() as conn:
        n = conn.execute("SELECT COUNT(*) FROM grading_records WHERE user_id=?", (uid,)).fetchone()[0]
        if giu_lich_su:
            conn.execute("UPDATE grading_records SET user_id=NULL, deleted_owner=? WHERE user_id=?",
                         (_label(target), uid))
        conn.execute("DELETE FROM users WHERE id=?", (uid,))  # lịch sử còn lại bị xoá theo (CASCADE)
    for kind in ("verify", "reset", "admin-link"):
        _unthrottle(kind, target["email"])  # cho phép đăng ký lại email này ngay
    db.log_admin(admin, "xoa_tai_khoan", _label(target),
                 f"{'giữ' if giu_lich_su else 'xoá'} {n} bệnh án đã chấm")
    return {"message": f"Đã xoá tài khoản {target['email']}"
                       + (f" (giữ lại {n} bệnh án trong lịch sử)." if giu_lich_su else f" và {n} bệnh án đã chấm.")}


# ── Lịch sử toàn hệ thống ──

def _record_date(rec: dict, created_at: str):
    try:
        return datetime.strptime(rec.get("thoi_gian", ""), "%d/%m/%Y %H:%M").date()
    except Exception:
        try:
            return datetime.strptime((created_at or "")[:10], "%Y-%m-%d").date()
        except Exception:
            return None


def _parse_date(s: str, name: str):
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(400, f"Ngày không hợp lệ ({name}).")


def _load_all_records(user_id: str = "", tu_ngay: str = "", den_ngay: str = "",
                      diem_tu: float = None, diem_den: float = None, q: str = "") -> list:
    d1, d2 = _parse_date(tu_ngay, "từ ngày"), _parse_date(den_ngay, "đến ngày")
    sql = """SELECT r.id, r.user_id, r.created_at, r.data, r.deleted_owner, u.ho_ten AS u_ten, u.email AS u_email
             FROM grading_records r LEFT JOIN users u ON u.id = r.user_id"""
    params = []
    if user_id == "khac":
        sql += " WHERE r.user_id IS NULL"
    elif user_id:
        if not str(user_id).isdigit():
            raise HTTPException(400, "Người chấm không hợp lệ.")
        sql += " WHERE r.user_id = ?"
        params.append(int(user_id))
    sql += " ORDER BY r.id DESC"
    with db.get_db() as conn:
        rows = conn.execute(sql, params).fetchall()

    q = (q or "").strip().lower()
    out = []
    for row in rows:
        rec = json.loads(row["data"])
        rec["id"] = row["id"]
        rec["user_id"] = row["user_id"]
        if row["user_id"]:
            rec["nguoi_cham"], rec["email_nguoi_cham"] = row["u_ten"], row["u_email"]
        elif row["deleted_owner"]:
            rec["nguoi_cham"], rec["email_nguoi_cham"] = f"{row['deleted_owner']} (đã xoá)", ""
        else:
            rec["nguoi_cham"], rec["email_nguoi_cham"] = "Lịch sử cũ (chưa có chủ)", ""
        d = _record_date(rec, row["created_at"])
        if d1 and (not d or d < d1):
            continue
        if d2 and (not d or d > d2):
            continue
        diem = float(rec.get("tong_diem") or 0)
        if diem_tu is not None and diem < diem_tu:
            continue
        if diem_den is not None and diem > diem_den:
            continue
        if q:
            hay = " ".join([str(rec.get("ten_file", "")), str((rec.get("benh_nhan") or {}).get("ho_ten", "")),
                            str(rec["nguoi_cham"]), str(rec["email_nguoi_cham"])]).lower()
            if q not in hay:
                continue
        out.append(rec)
    return out


def _summary(rec: dict) -> dict:
    bn = rec.get("benh_nhan") or {}
    return {
        "id": rec["id"], "ten_file": rec.get("ten_file", ""),
        "benh_nhan": {"ho_ten": bn.get("ho_ten", ""), "tuoi": bn.get("tuoi", ""), "gioi": bn.get("gioi", "")},
        "tong_diem": rec.get("tong_diem"), "xep_loai": rec.get("xep_loai", ""),
        "thoi_gian": rec.get("thoi_gian", ""), "user_id": rec.get("user_id"),
        "nguoi_cham": rec.get("nguoi_cham", ""), "email_nguoi_cham": rec.get("email_nguoi_cham", ""),
    }


def _filter_desc(user_id, tu_ngay, den_ngay, diem_tu, diem_den, q) -> str:
    parts = []
    if user_id:
        parts.append("người chấm=" + ("không còn tài khoản" if user_id == "khac" else f"#{user_id}"))
    if tu_ngay or den_ngay:
        parts.append(f"ngày {tu_ngay or '…'} → {den_ngay or '…'}")
    if diem_tu is not None or diem_den is not None:
        parts.append(f"điểm {diem_tu if diem_tu is not None else '…'}–{diem_den if diem_den is not None else '…'}")
    if q:
        parts.append(f"tìm '{q}'")
    return ", ".join(parts)


@router.get("/lich-su")
def lich_su_toan_he_thong(user_id: str = "", tu_ngay: str = "", den_ngay: str = "",
                          diem_tu: float = None, diem_den: float = None, q: str = "",
                          admin=Depends(get_admin_user)):
    recs = _load_all_records(user_id, tu_ngay, den_ngay, diem_tu, diem_den, q)
    diems = [float(r.get("tong_diem") or 0) for r in recs]
    # Ghi nhật ký việc xem (gộp: tối đa 1 dòng / 10 phút / admin)
    if not db.recent_admin_log(admin["id"], "xem_lich_su", 10):
        db.log_admin(admin, "xem_lich_su", "", _filter_desc(user_id, tu_ngay, den_ngay, diem_tu, diem_den, q))
    return {
        "tong_so": len(recs),
        "diem_tb": round(sum(diems) / len(diems), 2) if diems else None,
        "ti_le_dat": round(sum(1 for d in diems if d >= 5) / len(diems) * 100) if diems else None,
        "hien_thi": min(len(recs), MAX_LIST),
        "records": [_summary(r) for r in recs[:MAX_LIST]],
    }


@router.get("/lich-su/{rid}")
def chi_tiet_ban_ghi(rid: int, admin=Depends(get_admin_user)):
    with db.get_db() as conn:
        row = conn.execute("""SELECT r.id, r.user_id, r.data, r.deleted_owner, u.ho_ten AS u_ten, u.email AS u_email
                              FROM grading_records r LEFT JOIN users u ON u.id=r.user_id WHERE r.id=?""",
                           (rid,)).fetchone()
    if not row:
        raise HTTPException(404, "Không tìm thấy kết quả chấm.")
    rec = json.loads(row["data"])
    rec["id"] = row["id"]
    rec["nguoi_cham"] = row["u_ten"] or (f"{row['deleted_owner']} (đã xoá)" if row["deleted_owner"] else "Lịch sử cũ")
    rec["email_nguoi_cham"] = row["u_email"] or ""
    db.log_admin(admin, "xem_chi_tiet", f"#{rid} {rec.get('ten_file', '')}", f"người chấm: {rec['nguoi_cham']}")
    return rec


@router.get("/xuat-excel")
def xuat_excel_toan_he_thong(user_id: str = "", tu_ngay: str = "", den_ngay: str = "",
                             diem_tu: float = None, diem_den: float = None, q: str = "",
                             admin=Depends(get_admin_user)):
    recs = _load_all_records(user_id, tu_ngay, den_ngay, diem_tu, diem_den, q)
    if not recs:
        raise HTTPException(400, "Không có bệnh án nào khớp bộ lọc.")
    desc = _filter_desc(user_id, tu_ngay, den_ngay, diem_tu, diem_den, q)
    content = build_admin_excel([_summary(r) for r in recs], desc)
    db.log_admin(admin, "xuat_excel", "", f"{len(recs)} bệnh án" + (f"; {desc}" if desc else ""))
    fname = admin_excel_filename()
    return Response(content, media_type=XLSX_MIME,
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


# ── Đạo văn chéo ──

@router.post("/dao-van-cheo")
def dao_van_cheo(data: dict, admin=Depends(get_admin_user)):
    chi_khac = bool(data.get("chi_khac_nguoi_cham", True))
    tu_ngay, den_ngay = data.get("tu_ngay") or "", data.get("den_ngay") or ""
    recs = _load_all_records("", tu_ngay, den_ngay)
    if len(recs) < 2:
        return {"pairs": [], "so_benh_an": len(recs), "message": "Cần ít nhất 2 bệnh án để so sánh."}
    if len(recs) > MAX_SCAN:
        raise HTTPException(400, f"Có {len(recs)} bệnh án trong phạm vi quét (tối đa {MAX_SCAN}). "
                                 "Hãy thu hẹp khoảng thời gian.")
    recs.reverse()  # cũ → mới
    for r in recs:
        # Lịch sử cũ/đã xoá: mỗi nhóm coi như một "người chấm" riêng
        r["_owner"] = r["user_id"] if r["user_id"] else ("x:" + r["nguoi_cham"])
    started = now_vn()
    pairs = find_pairs(recs, owner_key="_owner", cross_only=chi_khac)
    secs = (now_vn() - started).total_seconds()
    pham_vi = (f"{tu_ngay or '…'} → {den_ngay or '…'}" if (tu_ngay or den_ngay) else "toàn bộ")
    db.log_admin(admin, "dao_van_cheo", "",
                 f"{len(recs)} bệnh án, phạm vi {pham_vi}, {'chỉ khác người chấm' if chi_khac else 'tất cả cặp'}: "
                 f"{len(pairs)} cặp")
    return {"pairs": pairs[:MAX_PAIRS], "tong_cap": len(pairs), "so_benh_an": len(recs),
            "thoi_gian_quet": round(secs, 1)}


# ── Sao lưu & nhật ký ──

@router.get("/sao-luu")
def sao_luu(admin=Depends(get_admin_user)):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        db.backup_to(path)
        with open(path, "rb") as f:
            content = f.read()
    finally:
        os.remove(path)
    db.log_admin(admin, "sao_luu", "", f"{round(len(content) / 1024)} KB")
    fname = f"benh_an_saoluu_{now_vn().strftime('%Y%m%d_%H%M')}.db"
    return Response(content, media_type="application/vnd.sqlite3",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@router.get("/nhat-ky")
def nhat_ky(limit: int = 200, admin=Depends(get_admin_user)):
    return db.list_admin_log(max(1, min(limit, 1000)))
