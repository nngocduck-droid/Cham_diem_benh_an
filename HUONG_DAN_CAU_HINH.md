# Hướng dẫn cập nhật: Tài khoản, Email & Quản trị (chỉ dùng Railway)

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

## Địa chỉ app – chỉ dùng Railway

Từ phiên bản này app **chỉ chạy trên Railway**: **https://chamdiembenhan-production.up.railway.app**
(kiểm tra lại địa chỉ đầy đủ: Railway → service **Cham_diem_benh_an** → **Settings** → **Networking**).

Server Railway đã có sẵn toàn bộ giao diện, nên không cần Netlify nữa. File `frontend/index.html` giờ chỉ là trang **tự chuyển sang địa chỉ Railway**, để ai còn giữ link Netlify cũ vẫn vào được app (xem Bước 6).

---

## Bước 1. Brevo – lấy API key gửi email (miễn phí)

Railway **chặn cổng SMTP** ở gói Free/Trial/Hobby (gói 5 USD/tháng là Hobby), nên app gửi email qua API HTTPS của Brevo (miễn phí 300 email/ngày).

1. Đăng ký tại https://www.brevo.com.
2. **Xác minh địa chỉ gửi:** vào **Senders, Domains & Dedicated IPs → Senders → Add a sender**, nhập email dùng để gửi (vd. Gmail của bạn) rồi bấm link xác minh Brevo gửi tới email đó.
3. **Tạo API key:** mở https://app.brevo.com/settings/keys/api (hoặc bấm tên tài khoản → **Settings → SMTP & API → API Keys & MCP**) → **Generate a new API key**:
   - **Access:** chọn **Restricted**.
   - **Permissions:** **Transactional email = All**; tất cả các dòng khác để **None**.
   - Không bật “Create MCP server API key”.
   - Bấm **Generate**, sao chép key ngay (bắt đầu bằng `xkeysib-…`; chỉ hiện một lần). Lấy ở tab *API keys*, không phải *SMTP*.
4. **Tắt chặn IP lạ:** tên tài khoản → **Settings → Security → Authorized IPs** → nếu đang *Activated* thì bấm **Deactivate blocking**. Lý do: địa chỉ IP của Railway thay đổi sau mỗi lần deploy, còn Brevo tự bật chặn IP lạ sau 30 ngày → email sẽ đột nhiên ngừng gửi.

> - Email gửi “từ” một địa chỉ @gmail.com qua Brevo đôi khi rơi vào mục Spam. Nếu có tên miền riêng và xác minh được trong Brevo, email sẽ vào hộp thư chính tốt hơn.
> - Brevo vô hiệu hoá key nếu **90 ngày** không có lần gửi nào (vd. nghỉ hè không ai dùng app). Khi đó tạo key mới và thay `BREVO_API_KEY` trên Railway.

## Bước 2. Railway – kiểm tra Volume (nơi lưu tài khoản & lịch sử)

Ổ đĩa của Railway bị xoá mỗi lần deploy, nên dữ liệu phải nằm trên Volume. Service **Cham_diem_benh_an** **đã có sẵn** Volume `cham_diem_benh_an-volume` (dòng nằm ngay dưới ô service) → không cần tạo thêm.

- Đường dẫn (*mount path*) của Volume là gì cũng được: Railway tự báo cho app qua biến `RAILWAY_VOLUME_MOUNT_PATH`, app tự lưu vào đó. **Không** cần đặt `DATA_DIR`.
- Nếu một ngày Volume bị gỡ, log khi khởi động sẽ có dòng cảnh báo `⚠️ Chưa gắn Volume trên Railway`.

## Bước 3. Railway – đặt biến môi trường

Bấm vào ô **Cham_diem_benh_an** → tab **Variables** → **Raw Editor** → **giữ nguyên** các dòng đang có (`GEMINI_API_KEY`, `APP_PASSWORD`…) và **thêm** các dòng sau:

```
BREVO_API_KEY=xkeysib-...(key ở bước 1)
MAIL_FROM=(email đã xác minh trong Brevo – Senders)
MAIL_FROM_NAME=Chấm Bệnh Án AI
SECRET_KEY=(chuỗi ngẫu nhiên – xem dưới)
APP_BASE_URL=https://chamdiembenhan-production.up.railway.app
ADMIN_EMAILS=(email bạn sẽ dùng làm admin)
```

| Biến | Ý nghĩa |
|---|---|
| `BREVO_API_KEY`, `MAIL_FROM` | gửi email qua Brevo; `MAIL_FROM` phải trùng sender đã xác minh |
| `SECRET_KEY` | khoá ký phiên đăng nhập và link email. Tạo trên Mac: mở **Terminal**, gõ `openssl rand -base64 48`. Đổi giá trị này thì mọi người phải đăng nhập lại |
| `APP_BASE_URL` | địa chỉ app dùng trong link xác nhận / đặt lại mật khẩu – phải đúng địa chỉ Railway ở trên |
| `ADMIN_EMAILS` | **admin gốc** (nhiều email cách nhau dấu phẩy) |
| `REGISTRATION_CODE` | *(tuỳ chọn)* mã đăng ký, xem dưới |
| `ALLOWED_EMAIL_DOMAINS` | *(tuỳ chọn)* chỉ cho đăng ký bằng email thuộc tên miền này, vd. `hmu.edu.vn` |

Sau khi sửa, Railway hiện nút **Deploy** (hoặc *Apply changes*) phía trên → bấm để áp dụng.

**Mã đăng ký:** để người lạ không tự tạo tài khoản và dùng hết hạn mức Gemini, app yêu cầu **mã đăng ký** khi đăng ký.
- Mặc định, mã đăng ký chính là `APP_PASSWORD` (mật khẩu chung đang dùng). Bạn chỉ cần báo mã này cho đồng nghiệp như trước.
- Đặt `REGISTRATION_CODE=...` để dùng mã khác, hoặc `REGISTRATION_CODE=off` để ai cũng đăng ký được (vẫn phải xác nhận email).

## Bước 4. Đưa code lên GitHub (Railway tự deploy)

Làm **sau** bước 1–3. Mở **Terminal** trên Mac:

```bash
cd ~/Documents/benh-an-app-v2
git add HUONG_DAN_CAU_HINH.md frontend/index.html backend/.env.example
git commit -m "Chỉ dùng Railway: hướng dẫn cấu hình, chuyển hướng bản Netlify"
git push
```

- Service **Cham_diem_benh_an** nối với repo GitHub `Cham_diem_benh_an`, nên mỗi lần `git push` Railway tự build và deploy (theo dõi ở tab **Deployments**, chờ trạng thái thành công).
- Railway tự cài thêm thư viện `itsdangerous` (đã có trong `requirements.txt`).

## Bước 5. Kiểm tra

1. Mở https://chamdiembenhan-production.up.railway.app/health → thấy `"email":"brevo"` là đã nhận cấu hình email.
2. Mở app → **Đăng ký** bằng email trong `ADMIN_EMAILS` (mã đăng ký = `APP_PASSWORD`) → nhận email → bấm **Xác nhận email** → menu trái có mục **🛡️ Quản trị**.
3. Chấm thử 1–2 bệnh án → kiểm tra hộp thư có email “Kết quả chấm bệnh án” kèm file Excel.
4. Nếu màn hình báo lỗi kiểu *“Dịch vụ email từ chối (401): …”* → xem lại API key / quyền *Transactional email* / sender ở bước 1.

## Bước 6. Ngừng dùng Netlify

- `frontend/index.html` giờ chỉ tự chuyển người dùng sang địa chỉ Railway.
  - Nếu site Netlify tự deploy từ GitHub: sau `git push` ở bước 4, link Netlify cũ sẽ tự chuyển sang Railway.
  - Nếu trước đây bạn kéo-thả thư mục lên Netlify: kéo-thả lại thư mục `frontend` một lần, **hoặc** xoá luôn site trên Netlify (*Site configuration → Delete this site*).
- Gửi địa chỉ mới cho đồng nghiệp: **https://chamdiembenhan-production.up.railway.app**

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
