from copy import deepcopy


def get_dashboard_payload() -> dict:
    payload = {
        "app_name": "Vigilant Curator",
        "system_name": "Exam Vigilance AI",
        "overview": {
            "integrity_score": "98.2%",
            "active_sessions": 1240,
            "rooms_online": 12,
            "alerts": [
                {
                    "icon": "warning",
                    "title": "Room B: Phone Detected",
                    "meta": "User ID: #ST-8821 • 2 mins ago",
                },
                {
                    "icon": "person_add",
                    "title": "Room D: Multiple Persons",
                    "meta": "User ID: #ST-9901 • 5 mins ago",
                },
            ],
        },
        "students": [
            {
                "name": "Lê Thị Minh Anh",
                "email": "minhanh.le@university.edu",
                "candidate_id": "SV2023-0042",
                "room": "P. Trực tuyến A2",
                "behaviors": ["Rời màn hình", "Nói chuyện"],
                "alerts": 12,
                "risk": "high",
            },
            {
                "name": "Nguyễn Văn Hoàng",
                "email": "hoang.nv@university.edu",
                "candidate_id": "SV2023-1109",
                "room": "P. Tập trung B4",
                "behaviors": ["Nhìn sang bên"],
                "alerts": 4,
                "risk": "medium",
            },
            {
                "name": "Trần Quốc Bảo",
                "email": "bao.tq@university.edu",
                "candidate_id": "SV2023-0852",
                "room": "P. Trực tuyến C1",
                "behaviors": ["Không ghi nhận"],
                "alerts": 0,
                "risk": "low",
            },
            {
                "name": "Phạm Tuyết Nhung",
                "email": "nhung.pt@university.edu",
                "candidate_id": "SV2023-0219",
                "room": "P. Trực tuyến A2",
                "behaviors": ["Vật thể lạ", "Khuôn mặt lạ"],
                "alerts": 21,
                "risk": "high",
            },
        ],
        "review_timeline": [
            {"time": "00:12:45", "label": "Sử dụng điện thoại", "confidence": "98%", "risk": "high"},
            {"time": "00:28:12", "label": "Nhiều người trong khung hình", "confidence": "84%", "risk": "high"},
            {"time": "00:35:55", "label": "Rời mắt khỏi màn hình", "confidence": "82%", "risk": "medium"},
            {"time": "01:04:22", "label": "Chuyển tab trình duyệt", "confidence": "100%", "risk": "high"},
        ],
    }
    return deepcopy(payload)
