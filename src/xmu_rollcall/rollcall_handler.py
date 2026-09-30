"""签到数据提取与分类（纯逻辑，无 I/O）。

v5 起本模块只做数据层面的判定：新签到由 core.py 注册进 AppState，
人工确认后由 core.py 执行签到。旧的自动签到流程（handle_rollcalls /
wait_for_classmates）已移除。
"""

TYPE_LABELS = {
    "number": "数字码签到",
    "radar": "雷达签到",
    "qr": "二维码签到",
}


def extract_rollcalls(data):
    """提取签到信息"""
    rollcalls = data['rollcalls']
    result = []
    if rollcalls:
        for rollcall in rollcalls:
            result.append({
                'course_title': rollcall['course_title'],
                'created_by_name': rollcall['created_by_name'],
                'department_name': rollcall['department_name'],
                'is_expired': rollcall['is_expired'],
                'is_number': rollcall['is_number'],
                'is_radar': rollcall['is_radar'],
                'rollcall_id': rollcall['rollcall_id'],
                'rollcall_status': rollcall['rollcall_status'],
                'scored': rollcall['scored'],
                'status': rollcall['status']
            })
    return len(rollcalls), result


def classify(rollcall):
    """判定签到类型：radar / number / qr（沿用旧 handle_rollcalls 的判定顺序）"""
    if rollcall.get("is_radar"):
        return "radar"
    if rollcall.get("is_number"):
        return "number"
    return "qr"


def type_label(kind):
    """类型的中文标签"""
    return TYPE_LABELS.get(kind, "签到")


def self_status(rollcall):
    """本人对该签到的状态（absent=未签 / on_call_fine=已签 等）"""
    return rollcall.get("status", "")
