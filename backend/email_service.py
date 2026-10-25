"""
Gửi email cho app.

Tự chọn cách gửi theo biến môi trường:
  1. BREVO_API_KEY  → gửi qua API HTTPS của Brevo (dùng được trên Railway gói Free/Hobby,
                       vì Railway chặn cổng SMTP ở các gói này).
  2. SMTP_HOST      → gửi qua SMTP (vd. Gmail + App Password; chỉ dùng được trên Railway gói Pro
                       hoặc khi chạy trên máy).
  3. Không có gì    → chế độ thử: chỉ in nội dung email ra log (không gửi thật).

Biến chung:
  MAIL_FROM       địa chỉ người gửi (với Brevo: phải là sender đã xác minh trong Brevo)
  MAIL_FROM_NAME  tên hiển thị (mặc định "Chấm Bệnh Án AI")
"""
import base64
import json
import logging
import os
import smtplib
import ssl
import urllib.error
import urllib.request
from email.message import EmailMessage
from email.utils import formataddr

log = logging.getLogger("benh_an.email")

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class EmailError(Exception):
    pass


def _cfg(name, default=""):
    return (os.getenv(name) or default).strip()


def provider() -> str:
    if _cfg("BREVO_API_KEY"):
        return "brevo"
    if _cfg("SMTP_HOST"):
        return "smtp"
    return "console"


def _sender():
    email = _cfg("MAIL_FROM") or _cfg("SMTP_USER")
    name = _cfg("MAIL_FROM_NAME", "Chấm Bệnh Án AI")
    if provider() != "console" and not email:
        raise EmailError("Chưa cấu hình MAIL_FROM (địa chỉ email người gửi).")
    return name, email


def send_email(to: str, subject: str, html: str, text: str = "", attachments=None):
    """attachments: list of (filename, bytes, mime_type)."""
    attachments = attachments or []
    p = provider()
    if p == "brevo":
        _send_brevo(to, subject, html, text, attachments)
    elif p == "smtp":
        _send_smtp(to, subject, html, text, attachments)
    else:
        log.warning(
            "[EMAIL – chế độ thử, KHÔNG gửi thật] Tới: %s | Tiêu đề: %s | Đính kèm: %s\n%s",
            to, subject, [a[0] for a in attachments], text or html,
        )
    log.info("Đã gửi email '%s' tới %s qua %s", subject, to, p)


def _send_brevo(to, subject, html, text, attachments):
    name, email = _sender()
    payload = {
        "sender": {"name": name, "email": email},
        "to": [{"email": to}],
        "subject": subject,
        "htmlContent": html,
    }
    if text:
        payload["textContent"] = text
    if attachments:
        payload["attachment"] = [
            {"name": fn, "content": base64.b64encode(data).decode()} for fn, data, _ in attachments
        ]
    url = _cfg("BREVO_API_URL", "https://api.brevo.com/v3/smtp/email")
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "api-key": _cfg("BREVO_API_KEY"),
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status >= 300:
                raise EmailError(f"Brevo trả về mã {resp.status}")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")[:300]
        log.error("Brevo lỗi %s: %s", e.code, detail)
        try:
            msg = json.loads(detail).get("message") or detail
        except Exception:
            msg = detail
        raise EmailError(f"Dịch vụ email từ chối ({e.code}): {msg}")
    except urllib.error.URLError as e:
        raise EmailError(f"Không kết nối được dịch vụ email: {e.reason}")


def _send_smtp(to, subject, html, text, attachments):
    name, email = _sender()
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((name, email))
    msg["To"] = to
    msg.set_content(text or "Vui lòng xem email này bằng trình đọc hỗ trợ HTML.")
    msg.add_alternative(html, subtype="html")
    for fn, data, mime in attachments:
        maintype, subtype = (mime or "application/octet-stream").split("/", 1)
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=fn)

    host = _cfg("SMTP_HOST")
    port = int(_cfg("SMTP_PORT", "587"))
    user = _cfg("SMTP_USER")
    password = _cfg("SMTP_PASSWORD")
    use_tls = _cfg("SMTP_USE_TLS", "true").lower() not in ("0", "false", "no")
    try:
        if port == 465:
            server = smtplib.SMTP_SSL(host, port, timeout=30, context=ssl.create_default_context())
        else:
            server = smtplib.SMTP(host, port, timeout=30)
            if use_tls:
                server.starttls(context=ssl.create_default_context())
        with server:
            if user and password:
                server.login(user, password)
            server.send_message(msg)
    except (smtplib.SMTPException, OSError) as e:
        log.error("SMTP lỗi: %s", e)
        raise EmailError(f"Không gửi được email qua SMTP: {e}")


# ── Mẫu email ──

def _layout(title: str, body_html: str) -> str:
    return f"""<!DOCTYPE html><html lang="vi"><body style="margin:0;padding:0;background:#f3f4f6;font-family:Arial,Helvetica,sans-serif;color:#1f2937">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f6;padding:24px 12px"><tr><td align="center">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;background:#ffffff;border-radius:12px;overflow:hidden;border:1px solid #e5e7eb">
<tr><td style="background:#1d4ed8;padding:18px 24px;color:#ffffff;font-size:16px;font-weight:bold">🏥 Chấm Bệnh Án AI</td></tr>
<tr><td style="padding:24px">
<h2 style="margin:0 0 14px;font-size:18px;color:#111827">{title}</h2>
{body_html}
</td></tr>
<tr><td style="padding:14px 24px;background:#f9fafb;color:#9ca3af;font-size:12px">Email này được gửi tự động từ ứng dụng Chấm Bệnh Án AI. Vui lòng không trả lời email này.</td></tr>
</table></td></tr></table></body></html>"""


def _button(url: str, label: str) -> str:
    return (f'<p style="margin:22px 0"><a href="{url}" style="background:#2563eb;color:#ffffff;'
            f'text-decoration:none;padding:12px 22px;border-radius:8px;font-weight:bold;display:inline-block">{label}</a></p>'
            f'<p style="font-size:12px;color:#6b7280;word-break:break-all">Nếu nút không bấm được, hãy sao chép đường link sau vào trình duyệt:<br>{url}</p>')


def _esc(s) -> str:
    return (str(s if s is not None else "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def send_verification_email(to: str, ho_ten: str, link: str, hours: int):
    html = _layout("Xác nhận địa chỉ email", f"""
<p>Chào {_esc(ho_ten)},</p>
<p>Cảm ơn bạn đã đăng ký tài khoản trên ứng dụng Chấm Bệnh Án AI. Vui lòng bấm nút dưới đây để xác nhận email và kích hoạt tài khoản:</p>
{_button(link, "Xác nhận email")}
<p style="font-size:13px;color:#6b7280">Đường link có hiệu lực trong {hours} giờ. Nếu bạn không đăng ký tài khoản này, hãy bỏ qua email.</p>""")
    text = (f"Chào {ho_ten},\n\nBấm vào link sau để xác nhận email và kích hoạt tài khoản "
            f"(hiệu lực {hours} giờ):\n{link}\n\nNếu bạn không đăng ký, hãy bỏ qua email này.")
    send_email(to, "Xác nhận đăng ký tài khoản – Chấm Bệnh Án AI", html, text)


def send_reset_email(to: str, ho_ten: str, link: str, minutes: int):
    html = _layout("Đặt lại mật khẩu", f"""
<p>Chào {_esc(ho_ten)},</p>
<p>Chúng tôi nhận được yêu cầu đặt lại mật khẩu cho tài khoản của bạn. Bấm nút dưới đây để đặt mật khẩu mới:</p>
{_button(link, "Đặt lại mật khẩu")}
<p style="font-size:13px;color:#6b7280">Đường link có hiệu lực trong {minutes} phút và chỉ dùng được một lần. Nếu bạn không yêu cầu, hãy bỏ qua email này – mật khẩu hiện tại vẫn giữ nguyên.</p>""")
    text = (f"Chào {ho_ten},\n\nĐặt mật khẩu mới tại link sau (hiệu lực {minutes} phút):\n{link}\n\n"
            "Nếu bạn không yêu cầu, hãy bỏ qua email này.")
    send_email(to, "Đặt lại mật khẩu – Chấm Bệnh Án AI", html, text)


def send_password_changed_email(to: str, ho_ten: str, when: str):
    html = _layout("Mật khẩu đã được thay đổi", f"""
<p>Chào {_esc(ho_ten)},</p>
<p>Mật khẩu tài khoản của bạn vừa được thay đổi lúc <strong>{_esc(when)}</strong>.</p>
<p>Nếu không phải bạn thực hiện, hãy dùng chức năng <strong>“Quên mật khẩu”</strong> trên trang đăng nhập để đặt lại mật khẩu ngay.</p>""")
    text = (f"Chào {ho_ten},\n\nMật khẩu tài khoản của bạn vừa được thay đổi lúc {when}.\n"
            "Nếu không phải bạn, hãy dùng chức năng 'Quên mật khẩu' để đặt lại ngay.")
    send_email(to, "Mật khẩu đã được thay đổi – Chấm Bệnh Án AI", html, text)


def send_results_email(to: str, ho_ten: str, results: list, plagiarism: list,
                       xlsx_bytes: bytes, filename: str, when: str, errors: list = None):
    errors = errors or []
    n = len(results)
    avg = sum(float(r.get("tong_diem") or 0) for r in results) / n if n else 0
    passed = sum(1 for r in results if float(r.get("tong_diem") or 0) >= 5)
    rows = "".join(
        f'<tr><td style="padding:6px 8px;border-bottom:1px solid #f3f4f6">{_esc(r.get("ten_file",""))}</td>'
        f'<td style="padding:6px 8px;border-bottom:1px solid #f3f4f6;text-align:center;font-weight:bold">{float(r.get("tong_diem") or 0):.2f}</td>'
        f'<td style="padding:6px 8px;border-bottom:1px solid #f3f4f6;text-align:center">{_esc(r.get("xep_loai",""))}</td></tr>'
        for r in results[:50]
    )
    more = f'<p style="font-size:12px;color:#6b7280">… và {n-50} bệnh án khác (xem trong file Excel).</p>' if n > 50 else ""
    loi_html = ""
    if errors:
        loi_rows = "".join(
            f'<tr><td style="padding:6px 8px;border-bottom:1px solid #fde2e1">{_esc(e.get("file",""))}</td>'
            f'<td style="padding:6px 8px;border-bottom:1px solid #fde2e1;color:#b91c1c">{_esc(e.get("loi",""))}'
            + (f'<div style="color:#6b7280;font-size:12px;margin-top:2px">→ {_esc(e.get("goi_y",""))}</div>' if e.get("goi_y") else "")
            + '</td></tr>'
            for e in errors
        )
        loi_html = (f'<p style="margin:16px 0 6px;font-weight:bold;color:#b91c1c">❌ {len(errors)} file lỗi – chưa được chấm '
                    f'(đã ghi trong file Excel, sheet “File lỗi”):</p>'
                    f'<table role="presentation" cellpadding="0" cellspacing="0" style="width:100%;border-collapse:collapse;'
                    f'font-size:13px;background:#fef2f2;border-radius:8px">{loi_rows}</table>')
    bang_ket_qua = ""
    if results:
        bang_ket_qua = (
            '<table role="presentation" cellpadding="0" cellspacing="0" style="width:100%;border-collapse:collapse;font-size:13px;margin-top:14px">'
            '<tr style="background:#f9fafb"><th style="padding:6px 8px;text-align:left">File</th>'
            '<th style="padding:6px 8px">Điểm</th><th style="padding:6px 8px">Xếp loại</th></tr>'
            f'{rows}</table>{more}'
        )
    nhan_loi = f" · {len(errors)} lỗi" if errors else ""
    plag = (f'<p style="background:#fef2f2;border:1px solid #fca5a5;color:#b91c1c;padding:10px 12px;border-radius:8px;font-size:13px">'
            f'⚠️ Phát hiện {len(plagiarism)} cặp bệnh án nghi vấn trùng lặp – xem sheet “Kiểm tra đạo văn”.</p>') if plagiarism else ""
    html = _layout("Kết quả chấm bệnh án", f"""
<p>Chào {_esc(ho_ten)},</p>
<p>Lần chấm lúc <strong>{_esc(when)}</strong> đã hoàn tất. File Excel tổng hợp được đính kèm trong email này.</p>
<table role="presentation" cellpadding="0" cellspacing="0" style="width:100%;margin:14px 0;font-size:13px">
<tr>
<td style="background:#eff6ff;border-radius:8px;padding:10px;text-align:center"><div style="font-size:20px;font-weight:bold;color:#1d4ed8">{n}</div><div style="color:#6b7280">đã chấm{nhan_loi}</div></td>
<td style="width:8px"></td>
<td style="background:#eff6ff;border-radius:8px;padding:10px;text-align:center"><div style="font-size:20px;font-weight:bold;color:#1d4ed8">{avg:.2f}</div><div style="color:#6b7280">điểm TB</div></td>
<td style="width:8px"></td>
<td style="background:#eff6ff;border-radius:8px;padding:10px;text-align:center"><div style="font-size:20px;font-weight:bold;color:#1d4ed8">{passed}/{n}</div><div style="color:#6b7280">đạt ≥ 5</div></td>
</tr></table>
{plag}
{loi_html}
{bang_ket_qua}""")
    text = (f"Chào {ho_ten},\n\nLần chấm lúc {when} đã hoàn tất: {n} bệnh án đã chấm, điểm TB {avg:.2f}, "
            f"đạt {passed}/{n}.")
    if errors:
        text += f"\n{len(errors)} file lỗi (chưa chấm):\n" + "\n".join(
            f"- {e.get('file','')}: {e.get('loi','')}" + (f" → {e.get('goi_y')}" if e.get("goi_y") else "")
            for e in errors)
    text += "\nFile Excel tổng hợp được đính kèm.\n"
    tieu_de = f"Kết quả chấm bệnh án – {when} ({n} bệnh án" + (f", {len(errors)} file lỗi)" if errors else ")")
    send_email(to, tieu_de, html, text, attachments=[(filename, xlsx_bytes, XLSX_MIME)])
