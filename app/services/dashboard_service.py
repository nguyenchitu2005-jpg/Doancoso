from copy import deepcopy


def get_dashboard_payload() -> dict:
    payload = {
        "app_name": "Vigilant Curator",
        "system_name": "Exam Vigilance AI",
        "overview": {
            "integrity_score": "0.0%",
            "active_sessions": 0,
            "rooms_online": 0,
            "alerts": [],
        },
        "students": [],
        "review_timeline": [],
    }
    return deepcopy(payload)
