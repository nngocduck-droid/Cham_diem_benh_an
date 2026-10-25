"""
Phát hiện bệnh án nghi vấn trùng lặp (dùng cho người dùng và cho admin quét toàn hệ thống).

Một cặp bị báo khi:
  - cùng bệnh nhân: trùng mã bệnh nhân, hoặc tên giống nhau > 80% (và tuổi không khác nhau), hoặc
  - nhận xét tổng thể giống nhau > 70%.

Các giá trị kiểu "Không có", "N/A", "Không rõ"... KHÔNG được coi là mã/tên bệnh nhân
(bản trước coi hai bệnh án cùng ghi "Không có" là cùng bệnh nhân → báo nhầm rất nhiều).
"""
import re
from collections import Counter
from difflib import SequenceMatcher

CONTENT_THRESHOLD = 0.7
NAME_THRESHOLD = 0.8
# Lọc nhanh: hai đoạn nhận xét chung < 35% cụm 2 từ thì độ giống ký tự luôn thấp (đo trên dữ liệu thật:
# độ giống > 0.6 chỉ xuất hiện khi tỉ lệ cụm từ chung >= 40%) → bỏ qua phép so sánh chậm.
BIGRAM_PREFILTER = 0.35

_PLACEHOLDERS = {
    "", "-", "--", "—", "–", "...", "…", "?", "x", "0", "na", "n/a", "n.a", "none", "null", "nil",
    "unknown", "chưa có", "chưa rõ", "chưa ghi", "trống", "ko", "k",
}


def _norm(s) -> str:
    s = re.sub(r"\s+", " ", str(s or "").strip().lower())
    return s.strip(" .,:;-_/")


def _meaningful(s) -> str:
    """Trả về chuỗi đã chuẩn hoá, hoặc '' nếu là giá trị giữ chỗ (không có thông tin)."""
    n = _norm(s)
    if n in _PLACEHOLDERS or n.startswith("không") or n.startswith("khong"):
        return ""
    return n


def _age(s):
    m = re.search(r"\d{1,3}", str(s or ""))
    return int(m.group()) if m else None


def _bigrams(text: str) -> set:
    words = re.findall(r"\w+", text)
    return set(zip(words, words[1:]))


def _ratio_if_above(a: str, ca: Counter, b: str, cb: Counter, threshold: float) -> float:
    """Tỉ lệ giống nhau (như SequenceMatcher.ratio) – trả 0 sớm nếu chắc chắn không vượt ngưỡng."""
    la, lb = len(a), len(b)
    if not la or not lb:
        return 0.0
    if 2.0 * min(la, lb) / (la + lb) <= threshold:          # real_quick_ratio
        return 0.0
    common = sum((ca & cb).values())
    if 2.0 * common / (la + lb) <= threshold:                # quick_ratio
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _content_ratio(a: dict, b: dict, sm_b: SequenceMatcher) -> float:
    """sm_b: SequenceMatcher đã set_seq2(b['text']) – tái sử dụng cho mọi a (nhanh hơn)."""
    if not a["text"] or not b["text"]:
        return 0.0
    ba, bb = a["bigrams"], b["bigrams"]
    if ba and bb and len(ba & bb) / min(len(ba), len(bb)) < BIGRAM_PREFILTER:
        return 0.0
    la, lb = len(a["text"]), len(b["text"])
    if 2.0 * min(la, lb) / (la + lb) <= CONTENT_THRESHOLD:
        return 0.0
    sm_b.set_seq1(a["text"])
    return sm_b.ratio()


def find_pairs(records: list, owner_key: str = None, cross_only: bool = False, limit: int = None) -> list:
    """records: list dict kết quả chấm. owner_key: tên trường chứa người chấm (cho admin).
    cross_only: chỉ báo các cặp của HAI người chấm khác nhau."""
    prepared = []
    for r in records:
        bn = r.get("benh_nhan") or {}
        text = _norm(r.get("nhan_xet_chung", ""))
        prepared.append({
            "rec": r,
            "name": _meaningful(bn.get("ho_ten")),
            "name_raw": bn.get("ho_ten", ""),
            "code": _meaningful(bn.get("ma_benh_nhan")).replace(" ", ""),
            "code_raw": bn.get("ma_benh_nhan", ""),
            "age": _age(bn.get("tuoi")),
            "sex": _norm(bn.get("gioi")),
            "text": text,
            "count": Counter(text),
            "bigrams": _bigrams(text),
            "name_count": None,
            "owner": r.get(owner_key) if owner_key else None,
        })

    for p in prepared:
        p["name_count"] = Counter(p["name"])

    pairs = []
    n = len(prepared)
    sm_b = SequenceMatcher(None)
    # Vòng ngoài theo b (chuỗi thứ 2 của SequenceMatcher, được lưu đệm), vòng trong theo a đứng trước b
    for j in range(1, n):
        b = prepared[j]
        sm_b.set_seq2(b["text"])
        for i in range(j):
            a = prepared[i]
            if cross_only and a["owner"] == b["owner"]:
                continue

            clues = []
            same_patient = False
            ages_differ = a["age"] is not None and b["age"] is not None and a["age"] != b["age"]

            if a["code"] and a["code"] == b["code"]:
                same_patient = True
                clues.append(f"Mã bệnh nhân trùng nhau: '{a['code_raw']}'")

            if a["name"] and b["name"] and not ages_differ:
                ns = _ratio_if_above(a["name"], a["name_count"], b["name"], b["name_count"], NAME_THRESHOLD)
                if ns > NAME_THRESHOLD:
                    same_patient = True
                    clues.append(f"Tên bệnh nhân giống nhau: '{a['name_raw']}' – '{b['name_raw']}'")

            if a["age"] is not None and not ages_differ and a["sex"] and a["sex"] == b["sex"]:
                clues.append(f"Cùng tuổi ({a['age']}) và giới tính ({a['rec'].get('benh_nhan', {}).get('gioi', '')})")

            if same_patient:
                if a["text"] and b["text"]:
                    sm_b.set_seq1(a["text"])
                    content_sim = sm_b.ratio()
                else:
                    content_sim = 0
            else:
                content_sim = _content_ratio(a, b, sm_b)

            if not (same_patient or content_sim > CONTENT_THRESHOLD):
                continue

            if content_sim > 0.85 or (same_patient and content_sim > 0.6):
                muc_do = "🔴 Rất cao"
            else:
                muc_do = "🟠 Cao"
            ra, rb = a["rec"], b["rec"]
            pair = {
                "sv1": ra.get("ten_file", ""),
                "sv2": rb.get("ten_file", ""),
                "id1": ra.get("id"),
                "id2": rb.get("id"),
                "muc_do": muc_do,
                "do_tuong_dong": round(content_sim * 100, 1),
                "chi_tiet": clues if clues else ["Nội dung bệnh án tương đồng cao"],
            }
            if owner_key:
                pair["nguoi_cham_1"] = ra.get("nguoi_cham", "")
                pair["nguoi_cham_2"] = rb.get("nguoi_cham", "")
                pair["thoi_gian_1"] = ra.get("thoi_gian", "")
                pair["thoi_gian_2"] = rb.get("thoi_gian", "")
            pairs.append(pair)

    # Nghiêm trọng nhất lên trước
    pairs.sort(key=lambda p: (p["muc_do"] != "🔴 Rất cao", -p["do_tuong_dong"]))
    return pairs[:limit] if limit else pairs
