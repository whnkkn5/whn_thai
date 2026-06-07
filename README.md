# 🏆 ระบบแข่งขันวันภาษาไทย

ระบบจัดการการแข่งขันวิชาการ พร้อมออกเกียรติบัตรอัตโนมัติ สำหรับศูนย์เครือข่ายโรงเรียน

## 🚀 Deploy ได้เลย

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/whnkkn5/whn_thai)

> คลิกปุ่มด้านบน → เข้าสู่ระบบ Render → Deploy ได้ทันที

---

## ฟีเจอร์หลัก

- **จัดการกิจกรรม** — เพิ่มกิจกรรมแยกตามระดับ ป.1-3 / ป.4-6 / ม.1-3
- **จัดการโรงเรียน** — บันทึกนักเรียนและครูแยกรายโรง
- **คณะกรรมการ** — กำหนดกรรมการตัดสินให้แต่ละกิจกรรม
- **กรอกคะแนน** — ระบบคิดรางวัลอัตโนมัติตามคะแนน
- **พิมพ์เกียรติบัตร** — 3 แบบ: นักเรียน / ครูผู้ฝึกสอน / กรรมการ

## ลำดับรางวัล

| คะแนน | รางวัล |
|---|---|
| อันดับ 1 (≥80) | 🥇 เหรียญทอง ชนะเลิศ |
| อันดับ 2 (≥80) | 🥇 เหรียญทอง รองชนะเลิศอันดับ 1 |
| อันดับ 3 (≥80) | 🥇 เหรียญทอง รองชนะเลิศอันดับ 2 |
| ≥ 80 | 🥇 เหรียญทอง |
| ≥ 70 | 🥈 เหรียญเงิน |
| ≥ 60 | 🥉 เหรียญทองแดง |
| < 60 | เข้าร่วม |

## วิธีรันบนเครื่องตัวเอง

```bash
git clone https://github.com/whnkkn5/whn_thai.git
cd whn_thai
pip install -r requirements.txt
python app.py
```

เปิดเบราว์เซอร์ที่ `http://127.0.0.1:5000`

## โครงสร้างโปรเจกต์

```
├── app.py              # Flask application หลัก
├── Procfile            # สำหรับ deploy (Render / Railway)
├── render.yaml         # Render configuration
├── requirements.txt    # Python dependencies
├── static/
│   └── style.css       # สไตล์และดีไซน์เกียรติบัตร
└── templates/          # HTML templates (11 หน้า)
```

## Tech Stack

- **Backend:** Python / Flask
- **Database:** SQLite
- **Frontend:** Bootstrap 5 + CSS print-ready
