"""
Tài khoản người dùng: đăng ký + xác nhận email, đăng nhập, đổi mật khẩu, quên mật khẩu.

Biến môi trường:
  SECRET_KEY            khoá ký token (nên đặt; nếu không, app tự tạo và lưu cạnh CSDL)
  APP_BASE_URL          địa chỉ app dùng trong link gửi qua email
                        (vd. https://benh-an-app-production.up.railway.app)
  REGISTRATION_CODE     mã đăng ký bắt buộc khi tạo tài khoản. Mặc định = APP_PASSWORD cũ,
                        để người lạ không tự đăng ký dùng app. Đặt REGISTRATION_CODE=off để mở hoàn toàn.
  ALLOWED_EMAIL_DOMAINS (tuỳ chọn) chỉ cho đăng ký với các tên miền này, vd. "hmu.edu.vn,gmail.com"
  ADMIN_EMAILS          email của admin gốc, cách nhau bởi dấu phẩy. Tài khoản có email này luôn là admin
                        (không thể bị khoá/xoá/thu quyền trong app). Lịch sử chấm cũ được gán cho admin gốc
                        đầu tiên đăng nhập. (OWNER_EMAIL của bản trước vẫn được chấp nhận.)
"""
import base64
import hashlib
import hmac
import logging
import os
import re
import secrets
import threading
import time
from thoi_gian import now_vn

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

import db
import email_service
from email_service import EmailError

log = logging.getLogger("benh_an.auth")
router = APIRouter()

SESSION_DAYS = int(os.getenv("SESSION_DAYS", "14"))
VERIFY_HOURS = 48
RESET_MINUTES = 60
EMAIL_COOLDOWN_SECONDS = 60
MIN_PASSWORD_LEN = 8
MAX_FILES_PER_BATCH = int(os.getenv("MAX_FILES_PER_BATCH", "12"))  # số file tối đa mỗi lần chấm
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ── Cấu hình ──

def _secret_key() -> str:
    key = os.getenv("SECRET_KEY", "").strip()
    if key:
        return key
    db.DATA_DIR.mkdir(parents=True, exist_ok=True)
    f = db.DATA_DIR / ".secret_key"
    if f.exists():
        return f.read_text().strip()
    key = secrets.token_urlsafe(48)
    f.write_text(key)
    log.warning("Chưa đặt SECRET_KEY – đã tự tạo và lưu tại %s", f)
    return key


_serializer = None


def serializer() -> URLSafeTimedSerializer:
    global _serializer
    if _serializer is None:
        _serializer = URLSafeTimedSerializer(_secret_key())
    return _serializer


def registration_code() -> str:
    code = os.getenv("REGISTRATION_CODE")
    if code is None:
        code = os.getenv("APP_PASSWORD", "")
    code = code.strip()
    return "" if code.lower() in ("", "off", "none", "khong") else code


def allowed_domains() -> list:
    raw = os.getenv("ALLOWED_EMAIL_DOMAINS", "")
    return [d.strip().lower().lstrip("@") for d in raw.split(",") if d.strip()]


def root_admin_emails() -> list:
    raw = os.getenv("ADMIN_EMAILS", "") + "," + os.getenv("OWNER_EMAIL", "")
    return [e.strip().lower() for e in raw.split(",") if e.strip()]


def is_root_admin(user) -> bool:
    return bool(user) and user["email"] in root_admin_emails()


def is_admin(user) -> bool:
    return bool(user) and (bool(user["is_admin"]) or is_root_admin(user))


def base_url(request: Request) -> str:
    env = os.getenv("APP_BASE_URL", "").strip().rstrip("/")
    if env:
        return env
    proto = request.headers.get("x-forwarded-proto", request.url.scheme).split(",")[0].strip()
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"{proto}://{host}"


# ── Mật khẩu ──

PBKDF2_ITERATIONS = 310_000


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        PBKDF2_ITERATIONS, base64.b64encode(salt).decode(), base64.b64encode(dk).decode()
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt_b64, hash_b64 = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), base64.b64decode(salt_b64), int(iters)
        )
        return hmac.compare_digest(dk, base64.b64decode(hash_b64))
    except Exception:
        return False


def _pw_fingerprint(password_hash: str) -> str:
    """Đổi mật khẩu → dấu vân tay đổi → mọi phiên đăng nhập cũ và link đặt lại cũ hết hiệu lực."""
    return hashlib.sha256(password_hash.encode()).hexdigest()[:16]


def _check_new_password(pw: str):
    if len(pw or "") < MIN_PASSWORD_LEN:
        raise HTTPException(400, f"Mật khẩu phải có ít nhất {MIN_PASSWORD_LEN} ký tự.")
    if len(pw) > 200:
        raise HTTPException(400, "Mật khẩu quá dài.")


# ── Token ──

def make_session_token(user) -> str:
    return serializer().dumps(
        {"uid": user["id"], "pv": _pw_fingerprint(user["password_hash"])}, salt="session"
    )


def _make_verify_token(user) -> str:
    return serializer().dumps({"uid": user["id"], "email": user["email"]}, salt="verify-email")


def _make_reset_token(user) -> str:
    return serializer().dumps(
        {"uid": user["id"], "pv": _pw_fingerprint(user["password_hash"])}, salt="reset-password"
    )


def _load_token(token: str, salt: str, max_age: int, expired_msg: str, invalid_msg: str) -> dict:
    try:
        return serializer().loads(token or "", salt=salt, max_age=max_age)
    except SignatureExpired:
        raise HTTPException(400, expired_msg)
    except BadSignature:
        raise HTTPException(400, invalid_msg)


# ── Truy vấn người dùng ──

def _get_user_by_email(email: str):
    with db.get_db() as conn:
        return conn.execute("SELECT * FROM users WHERE email=?", (email.lower(),)).fetchone()


def _get_user(uid: int):
    with db.get_db() as conn:
        return conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()


def public_user(u) -> dict:
    return {
        "id": u["id"],
        "email": u["email"],
        "ho_ten": u["ho_ten"],
        "email_ket_qua": bool(u["email_ket_qua"]),
        "created_at": u["created_at"],
        "la_admin": is_admin(u),
        "la_admin_goc": is_root_admin(u),
    }


LOCKED_MSG = "Tài khoản của bạn đã bị khoá. Vui lòng liên hệ quản trị viên."


def get_current_user(authorization: str = Header(default="")):
    token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
    if not token:
        raise HTTPException(401, "Vui lòng đăng nhập.")
    try:
        data = serializer().loads(token, salt="session", max_age=SESSION_DAYS * 86400)
    except (BadSignature, SignatureExpired):
        raise HTTPException(401, "Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại.")
    user = _get_user(data.get("uid", 0))
    if not user or not user["is_verified"] or data.get("pv") != _pw_fingerprint(user["password_hash"]):
        raise HTTPException(401, "Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại.")
    if user["is_locked"]:
        raise HTTPException(401, LOCKED_MSG)
    return user


def get_admin_user(user=Depends(get_current_user)):
    if not is_admin(user):
        raise HTTPException(403, "Chỉ quản trị viên mới dùng được chức năng này.")
    return user


# ── Chống spam email (trong bộ nhớ) ──

_last_sent = {}
_lock = threading.Lock()


def _throttle(kind: str, email: str):
    key = f"{kind}:{email}"
    now = time.time()
    with _lock:
        last = _last_sent.get(key, 0)
        wait = int(EMAIL_COOLDOWN_SECONDS - (now - last))
        if wait > 0:
            raise HTTPException(429, f"Vui lòng đợi {wait} giây trước khi gửi lại email.")
        _last_sent[key] = now


def _unthrottle(kind: str, email: str):
    """Gửi thất bại thì cho phép thử lại ngay."""
    with _lock:
        _last_sent.pop(f"{kind}:{email}", None)


def _norm_email(email) -> str:
    email = (email or "").strip().lower()
    if not EMAIL_RE.match(email) or len(email) > 254:
        raise HTTPException(400, "Địa chỉ email không hợp lệ.")
    return email


def _send_verification(request: Request, user):
    link = f"{base_url(request)}/?verify_token={_make_verify_token(user)}"
    email_service.send_verification_email(user["email"], user["ho_ten"], link, VERIFY_HOURS)


def send_account_link(request: Request, user) -> str:
    """Gửi link đặt lại mật khẩu (tài khoản đã xác nhận) hoặc link xác nhận (chưa xác nhận).
    Trả về loại email đã gửi. Dùng cho 'Quên mật khẩu' và cho admin."""
    if user["is_verified"]:
        link = f"{base_url(request)}/?reset_token={_make_reset_token(user)}"
        email_service.send_reset_email(user["email"], user["ho_ten"], link, RESET_MINUTES)
        return "reset"
    _send_verification(request, user)
    return "verify"


# ── Endpoints ──

@router.get("/cau-hinh")
def cau_hinh():
    return {
        "can_ma_dang_ky": bool(registration_code()),
        "ten_mien_cho_phep": allowed_domains(),
        "mat_khau_toi_thieu": MIN_PASSWORD_LEN,
        "so_file_toi_da": MAX_FILES_PER_BATCH,
    }


@router.post("/dang-ky")
def dang_ky(data: dict, request: Request):
    ho_ten = (data.get("ho_ten") or "").strip()
    email = _norm_email(data.get("email"))
    mat_khau = data.get("mat_khau") or ""

    code = registration_code()
    if code and not hmac.compare_digest((data.get("ma_dang_ky") or "").strip(), code):
        raise HTTPException(403, "Mã đăng ký không đúng. Vui lòng liên hệ quản trị viên để lấy mã.")
    domains = allowed_domains()
    if domains and email.split("@")[1] not in domains:
        raise HTTPException(400, "Chỉ chấp nhận email thuộc: " + ", ".join(domains))
    if not ho_ten:
        raise HTTPException(400, "Vui lòng nhập họ tên.")
    if len(ho_ten) > 100:
        raise HTTPException(400, "Họ tên quá dài.")
    _check_new_password(mat_khau)

    existing = _get_user_by_email(email)
    if existing and existing["is_verified"]:
        raise HTTPException(409, "Email này đã có tài khoản. Hãy đăng nhập hoặc dùng “Quên mật khẩu”.")

    _throttle("verify", email)
    pw_hash = hash_password(mat_khau)
    with db.get_db() as conn:
        if existing:  # đăng ký lại khi chưa xác nhận → cập nhật thông tin
            conn.execute("UPDATE users SET ho_ten=?, password_hash=? WHERE id=?",
                         (ho_ten, pw_hash, existing["id"]))
        else:
            conn.execute(
                "INSERT INTO users(email, ho_ten, password_hash, created_at) VALUES (?,?,?,?)",
                (email, ho_ten, pw_hash, db.now_str()),
            )
    user = _get_user_by_email(email)
    try:
        _send_verification(request, user)
    except EmailError as e:
        _unthrottle("verify", email)
        raise HTTPException(502, f"Đã tạo tài khoản nhưng chưa gửi được email xác nhận: {e}. "
                                 "Vui lòng thử “Gửi lại email xác nhận” sau ít phút.")
    return {"message": f"Đã gửi email xác nhận tới {email}. Vui lòng kiểm tra hộp thư (cả mục Spam).",
            "email": email}


@router.post("/gui-lai-xac-nhan")
def gui_lai_xac_nhan(data: dict, request: Request):
    email = _norm_email(data.get("email"))
    user = _get_user_by_email(email)
    if user and not user["is_verified"]:
        _throttle("verify", email)
        try:
            _send_verification(request, user)
        except EmailError as e:
            _unthrottle("verify", email)
            raise HTTPException(502, f"Chưa gửi được email: {e}")
    return {"message": "Nếu email đã đăng ký và chưa xác nhận, một email xác nhận mới đã được gửi."}


@router.post("/xac-nhan-email")
def xac_nhan_email(data: dict):
    payload = _load_token(
        data.get("token"), "verify-email", VERIFY_HOURS * 3600,
        "Link xác nhận đã hết hạn. Vui lòng đăng ký lại hoặc bấm “Gửi lại email xác nhận”.",
        "Link xác nhận không hợp lệ.",
    )
    user = _get_user(payload.get("uid", 0))
    if not user or user["email"] != payload.get("email"):
        raise HTTPException(400, "Link xác nhận không hợp lệ.")
    if user["is_locked"]:
        raise HTTPException(423, LOCKED_MSG)
    if user["is_verified"]:
        return {"message": "Email đã được xác nhận trước đó. Vui lòng đăng nhập.", "da_xac_nhan_truoc": True}
    with db.get_db() as conn:
        conn.execute("UPDATE users SET is_verified=1, verified_at=?, last_login_at=? WHERE id=?",
                     (db.now_str(), db.now_str(), user["id"]))
    user = _get_user(user["id"])
    _claim_if_owner(user)
    return {"message": "Xác nhận email thành công! Tài khoản đã được kích hoạt.",
            "token": make_session_token(user), "user": public_user(user)}


def _claim_if_owner(user):
    if is_root_admin(user):
        n = db.claim_orphan_records(user["id"])
        if n:
            log.info("Đã gán %d bản ghi lịch sử cũ cho %s", n, user["email"])


@router.post("/login")
def login(data: dict):
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    if not email or not password:
        raise HTTPException(400, "Vui lòng nhập email và mật khẩu.")
    user = _get_user_by_email(email)
    if not user or not verify_password(password, user["password_hash"]):
        raise HTTPException(401, "Email hoặc mật khẩu không đúng.")
    if user["is_locked"]:
        raise HTTPException(423, LOCKED_MSG)
    if not user["is_verified"]:
        raise HTTPException(403, "Tài khoản chưa xác nhận email. Vui lòng kiểm tra hộp thư hoặc gửi lại email xác nhận.")
    with db.get_db() as conn:
        conn.execute("UPDATE users SET last_login_at=? WHERE id=?", (db.now_str(), user["id"]))
    _claim_if_owner(user)
    return {"success": True, "token": make_session_token(user), "user": public_user(user)}


@router.get("/toi")
def toi(user=Depends(get_current_user)):
    return public_user(user)


@router.put("/toi")
def cap_nhat_toi(data: dict, user=Depends(get_current_user)):
    ho_ten = data.get("ho_ten")
    email_ket_qua = data.get("email_ket_qua")
    with db.get_db() as conn:
        if ho_ten is not None:
            ho_ten = str(ho_ten).strip()
            if not ho_ten or len(ho_ten) > 100:
                raise HTTPException(400, "Họ tên không hợp lệ.")
            conn.execute("UPDATE users SET ho_ten=? WHERE id=?", (ho_ten, user["id"]))
        if email_ket_qua is not None:
            conn.execute("UPDATE users SET email_ket_qua=? WHERE id=?",
                         (1 if email_ket_qua else 0, user["id"]))
    return public_user(_get_user(user["id"]))


def _notify_password_changed(user):
    try:
        email_service.send_password_changed_email(
            user["email"], user["ho_ten"], now_vn().strftime("%H:%M %d/%m/%Y"))
    except EmailError as e:
        log.warning("Không gửi được email báo đổi mật khẩu: %s", e)


@router.post("/doi-mat-khau")
def doi_mat_khau(data: dict, background: BackgroundTasks, user=Depends(get_current_user)):
    cu = data.get("mat_khau_cu") or ""
    moi = data.get("mat_khau_moi") or ""
    if not verify_password(cu, user["password_hash"]):
        raise HTTPException(400, "Mật khẩu hiện tại không đúng.")
    _check_new_password(moi)
    if cu == moi:
        raise HTTPException(400, "Mật khẩu mới phải khác mật khẩu hiện tại.")
    with db.get_db() as conn:
        conn.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(moi), user["id"]))
    user = _get_user(user["id"])
    background.add_task(_notify_password_changed, user)
    return {"message": "Đổi mật khẩu thành công. Các phiên đăng nhập khác đã bị đăng xuất.",
            "token": make_session_token(user)}


@router.post("/quen-mat-khau")
def quen_mat_khau(data: dict, request: Request):
    email = _norm_email(data.get("email"))
    user = _get_user_by_email(email)
    if user and not user["is_locked"]:
        _throttle("reset", email)
        try:
            send_account_link(request, user)
        except EmailError as e:
            _unthrottle("reset", email)
            raise HTTPException(502, f"Chưa gửi được email: {e}")
    return {"message": "Nếu email đã đăng ký, chúng tôi đã gửi link đặt lại mật khẩu. Vui lòng kiểm tra hộp thư (cả mục Spam)."}


@router.post("/dat-lai-mat-khau")
def dat_lai_mat_khau(data: dict, background: BackgroundTasks):
    payload = _load_token(
        data.get("token"), "reset-password", RESET_MINUTES * 60,
        "Link đặt lại mật khẩu đã hết hạn. Vui lòng yêu cầu link mới.",
        "Link đặt lại mật khẩu không hợp lệ.",
    )
    user = _get_user(payload.get("uid", 0))
    if not user or payload.get("pv") != _pw_fingerprint(user["password_hash"]):
        raise HTTPException(400, "Link đặt lại mật khẩu đã được dùng hoặc không còn hiệu lực.")
    if user["is_locked"]:
        raise HTTPException(423, LOCKED_MSG)
    moi = data.get("mat_khau_moi") or ""
    _check_new_password(moi)
    with db.get_db() as conn:
        conn.execute(
            "UPDATE users SET password_hash=?, is_verified=1, verified_at=COALESCE(verified_at, ?), "
            "last_login_at=? WHERE id=?",
            (hash_password(moi), db.now_str(), db.now_str(), user["id"]),
        )
    user = _get_user(user["id"])
    background.add_task(_notify_password_changed, user)
    return {"message": "Đặt lại mật khẩu thành công.", "token": make_session_token(user),
            "user": public_user(user)}
