# Hướng dẫn cấu hình: Tài khoản, Email & Quản trị

Phiên bản này thêm:

1. **Đăng ký + xác nhận email**: người dùng tự đăng ký (họ tên, email, mật khẩu, mã đăng ký). App gửi link xác nhận; bấm link thì tài khoản được kích hoạt và đăng nhập luôn.
2. **Đổi mật khẩu** (trang *Tài khoản*) và **Quên mật khẩu** (gửi link đặt lại qua email, hiệu lực 60 phút, dùng một lần). Sau khi đổi mật khẩu, các phiên đăng nhập khác bị đăng xuất và người dùng nhận email thông báo.
3. **Gửi file Excel tổng hợp qua email sau mỗi lần chấm** (tự động, có thể tắt trong *Tài khoản*), kèm nút **“✉ Gửi qua email”** để gửi lại.

4. **Tài khoản admin** với trang **Quản trị**: quản lý người dùng, xem lịch sử chấm toàn hệ thống, kiểm tra đạo văn chéo giữa các giảng viên, sao lưu dữ liệu, nhật ký quản trị (xem mục *Tài khoản admin* bên dưới).

Ngoài ra:
- Mỗi người chỉ thấy **lịch sử chấm của mình**; kiểm tra đạo văn cũng chạy trong lịch sử của từng người.
- Mọi API (chấm, lịch sử, xuất Excel) giờ đều **yêu cầu đăng nhập**. Bản cũ không kiểm tra, ai có địa chỉ server cũng gọi được.
- Khi một người đang chấm, người khác vẫn dùng app bình thường (trước đây cả server bị “treo” trong lúc gọi Gemini).
- Giờ hiển thị trong app, Excel và email dùng **giờ Việt Nam** (server Railway chạy giờ UTC).
- **Sửa lỗi kiểm tra đạo văn:** bản cũ coi hai bệnh án cùng ghi mã bệnh nhân “Không có”/“N/A” là *cùng bệnh nhân* → với 46 bệnh án mẫu, bản cũ báo 366 cặp nghi vấn; sau khi sửa còn 90 cặp (chủ yếu là nhiều sinh viên cùng làm bệnh án trên một bệnh nhân). Nay các giá trị kiểu “Không có” được bỏ qua, và tên giống nhau nhưng tuổi khác nhau không còn bị coi là cùng bệnh nhân.
- **Bảo mật hiển thị:** tên file, họ tên và nhận xét của AI được hiển thị dạng chữ thuần, nên một tên file hay nội dung PDF có chèn mã HTML/JavaScript không thể chạy trên trình duyệt người xem (quan trọng khi admin xem bệnh án của người khác).

---

## Bước 1. Gắn Volume trên Railway (bắt buộc)

Tài khoản và lịch sử được lưu trong file CSDL SQLite. **Ổ đĩa của Railway bị xoá mỗi lần deploy**, nên phải gắn Volume, nếu không mọi tài khoản sẽ mất sau mỗi lần cập nhật.

1. Mở project trên Railway → bấm chuột phải vào service backend (hoặc **+ New → Volume**).
2. Chọn service backend, đặt **Mount path** là `/data`.
3. Không cần đặt thêm biến nào: Railway tự cung cấp `RAILWAY_VOLUME_MOUNT_PATH` và app tự dùng thư mục đó.

> Nếu quên bước này, log khi khởi động sẽ có dòng cảnh báo `⚠️ Chưa gắn Volume trên Railway`.

## Bước 2. Tạo tài khoản gửi email Brevo (miễn phí)

Railway **chặn cổng SMTP** ở gói Free/Trial/Hobby, nên không gửi trực tiếp qua Gmail được. App gửi qua API HTTPS của Brevo (gói miễn phí: 300 email/ngày).

1. Đăng ký tại https://www.brevo.com.
2. Vào **Senders, Domains & Dedicated IPs → Senders → Add a sender**, nhập email dùng để gửi (vd. Gmail của bạn) và bấm link xác minh Brevo gửi tới email đó.
3. Vào **SMTP & API → API Keys → Generate a new API key**, sao chép key (bắt đầu bằng `xkeysib-...`).

> Email gửi từ địa chỉ @gmail.com qua dịch vụ trung gian đôi khi rơi vào mục Spam ở lần đầu. Nếu trường có tên miền riêng (vd. @hmu.edu.vn) và bạn xác minh được tên miền đó trong Brevo, email sẽ vào hộp thư chính tốt hơn.

*Cách khác:* nếu dùng Railway gói Pro hoặc chạy trên máy riêng, có thể dùng SMTP của Gmail (`SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=587`, `SMTP_USER`, `SMTP_PASSWORD` là *App Password* 16 ký tự). Xem `backend/.env.example`.

## Bước 3. Đặt biến môi trường trên Railway

Service backend → **Variables** → thêm:

| Biến | Giá trị | Ghi chú |
|---|---|---|
| `BREVO_API_KEY` | key ở bước 2 | |
| `MAIL_FROM` | email đã xác minh trong Brevo | phải khớp sender đã xác minh |
| `MAIL_FROM_NAME` | `Chấm Bệnh Án AI` | tuỳ chọn |
| `SECRET_KEY` | chuỗi ngẫu nhiên dài | tạo bằng `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`. Đổi giá trị này thì mọi người phải đăng nhập lại |
| `APP_BASE_URL` | `https://benh-an-app-production.up.railway.app` | địa chỉ dùng trong link email |
| `ADMIN_EMAILS` | email của bạn | **admin gốc**; nhiều email cách nhau bởi dấu phẩy. Lịch sử chấm cũ (nếu còn) thuộc admin gốc đăng nhập đầu tiên |
| `REGISTRATION_CODE` | mã bạn muốn | tuỳ chọn, xem bên dưới |
| `ALLOWED_EMAIL_DOMAINS` | vd. `hmu.edu.vn` | tuỳ chọn: chỉ cho đăng ký bằng email thuộc tên miền này |

**Mã đăng ký:** để người lạ không tự tạo tài khoản và dùng hết hạn mức Gemini, app yêu cầu **mã đăng ký** khi đăng ký.
- Mặc định, mã đăng ký chính là `APP_PASSWORD` (mật khẩu chung đang dùng). Bạn chỉ cần báo mã này cho đồng nghiệp như trước.
- Đặt `REGISTRATION_CODE=...` để dùng mã khác, hoặc `REGISTRATION_CODE=off` để ai cũng đăng ký được (vẫn phải xác nhận email).

## Bước 4. Deploy

```bash
cd ~/Documents/benh-an-app-v2
git add backend frontend HUONG_DAN_CAU_HINH.md .gitignore
git commit -m "Thêm tài khoản, xác nhận email, đổi mật khẩu, gửi Excel qua email, trang quản trị"
git push
```

- Railway tự cài thêm thư viện `itsdangerous` (đã có trong `requirements.txt`).
- Nếu dùng bản Netlify, deploy lại thư mục `frontend/` (file `frontend/index.html` giống `backend/static/index.html`, chỉ khác dòng địa chỉ API).
- **Nên đặt biến môi trường và gắn Volume trước khi push**, để ngay khi bản mới chạy thì đăng ký và gửi email hoạt động.

## Bước 5. Kiểm tra

1. Mở `https://<app>/health` → thấy `"email":"brevo"` là đã nhận cấu hình email.
2. Đăng ký tài khoản bằng email của bạn (trùng `ADMIN_EMAILS`) → nhận email → bấm **Xác nhận email** → menu bên trái có mục **🛡️ Quản trị**.
3. Chấm thử 1–2 bệnh án → kiểm tra hộp thư có email “Kết quả chấm bệnh án” kèm file Excel.

## Lưu ý khi chuyển đổi

- Mật khẩu chung cũ **không còn dùng để đăng nhập**. Mỗi người cần tự đăng ký tài khoản (dùng mật khẩu chung cũ làm mã đăng ký nếu bạn giữ mặc định).
- Lịch sử cũ trong `lich_su_cham.json` (nếu file còn trên server) được nhập một lần và gán cho admin gốc đầu tiên đăng nhập. Trên Railway file này thường đã mất qua các lần deploy trước.
- Nếu trước đó bạn đã đặt `OWNER_EMAIL`, biến này vẫn được hiểu như `ADMIN_EMAILS`.

## Tài khoản admin

**Cách có tài khoản admin:** đặt `ADMIN_EMAILS` trên Railway rồi đăng ký bình thường bằng email đó. Không có màn hình đăng nhập riêng – admin dùng chung màn hình đăng nhập, chỉ có thêm mục **Quản trị** trong menu.

**Hai loại admin**

| | Admin gốc (`ADMIN_EMAILS`) | Admin được cấp trong app |
|---|---|---|
| Xem lịch sử, đạo văn chéo, sao lưu, nhật ký | ✅ | ✅ |
| Khoá/xoá/xác nhận… tài khoản người dùng thường | ✅ | ✅ |
| Cấp/thu quyền admin | ✅ | ❌ |
| Khoá/xoá tài khoản admin khác | ✅ (trừ admin gốc) | ❌ |
| Bị khoá/xoá/thu quyền trong app | ❌ không thể | ✅ bởi admin gốc |

Không ai tự khoá/tự xoá/tự thu quyền của chính mình được. Muốn bỏ một admin gốc thì xoá email khỏi `ADMIN_EMAILS` trên Railway.

**Trang Quản trị có 4 tab**

1. **Người dùng** – thống kê nhanh (tổng tài khoản, chờ xác nhận, bị khoá, bệnh án đã chấm) và danh sách tài khoản kèm số bệnh án đã chấm, lần đăng nhập gần nhất. Thao tác:
   - *Xác nhận email* thay người dùng (khi email xác nhận rơi vào Spam hoặc bị máy chủ email của trường chặn);
   - *Gửi link đặt lại mật khẩu* (hoặc gửi lại link xác nhận nếu chưa xác nhận);
   - *Khoá / Mở khoá* – khoá là bị đăng xuất ngay, lịch sử vẫn giữ;
   - *Cấp / Thu quyền admin* (chỉ admin gốc);
   - *Xoá* – chọn giữ lại hoặc xoá luôn các bệnh án người đó đã chấm.
   Số trên mục **Quản trị** ở menu = số tài khoản đang chờ xác nhận email.
2. **Lịch sử toàn hệ thống** – mọi bệnh án đã chấm, lọc theo người chấm, khoảng ngày, khoảng điểm, tên file/bệnh nhân; xem chi tiết; **Xuất Excel** (sheet *Tổng hợp* có cột người chấm + sheet *Theo người chấm*: số bệnh án, điểm TB, thấp/cao nhất, tỉ lệ đạt).
3. **Đạo văn chéo** – so sánh bệnh án *giữa các người chấm khác nhau* (sinh viên nộp cùng một bệnh án cho hai giảng viên). Có thể bỏ chọn để so tất cả các cặp. Mỗi lần quét tối đa 1.500 bệnh án; nên chọn khoảng thời gian (một đợt thực tập).
4. **Sao lưu & nhật ký** – tải file `.db` chứa toàn bộ dữ liệu (mở bằng *DB Browser for SQLite*); nhật ký ghi mọi thao tác quản trị, kể cả việc **xem** bệnh án của người khác (vì có thông tin bệnh nhân).

> Khôi phục từ bản sao lưu chưa có trong giao diện. Nên tải bản sao lưu định kỳ (vd. cuối mỗi đợt thực tập).

## Chạy trên máy (thử nghiệm)

```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --reload
```

Không đặt `BREVO_API_KEY`/`SMTP_HOST` thì app ở **chế độ thử**: email không được gửi thật mà in ra terminal, bạn sao chép link xác nhận từ log để dùng.

## Các API mới

| Phương thức | Đường dẫn | Mô tả |
|---|---|---|
| GET | `/cau-hinh` | app có yêu cầu mã đăng ký / giới hạn tên miền không |
| POST | `/dang-ky` | `{ho_ten, email, mat_khau, ma_dang_ky}` |
| POST | `/xac-nhan-email` | `{token}` từ link email → trả token đăng nhập |
| POST | `/gui-lai-xac-nhan` | `{email}` |
| POST | `/login` | `{email, password}` → `{token, user}` |
| GET/PUT | `/toi` | xem / sửa thông tin (`ho_ten`, `email_ket_qua`) |
| POST | `/doi-mat-khau` | `{mat_khau_cu, mat_khau_moi}` |
| POST | `/quen-mat-khau` | `{email}` |
| POST | `/dat-lai-mat-khau` | `{token, mat_khau_moi}` |
| POST | `/gui-email-ket-qua` | `{record_ids, plagiarism}` → gửi Excel tới email người dùng |

API quản trị (chỉ admin): `GET /admin/tong-quan`, `GET /admin/nguoi-dung`, `POST /admin/nguoi-dung/{id}/khoa|xac-nhan|gui-link|quyen-admin`, `DELETE /admin/nguoi-dung/{id}?giu_lich_su=true`, `GET /admin/lich-su`, `GET /admin/lich-su/{id}`, `GET /admin/xuat-excel`, `POST /admin/dao-van-cheo`, `GET /admin/sao-luu`, `GET /admin/nhat-ky`.

Các API cũ (`/cham-nhieu`, `/lich-su`, `/kiem-tra-dao-van`, `/xuat-excel-nhieu`) cần header `Authorization: Bearer <token>`.
