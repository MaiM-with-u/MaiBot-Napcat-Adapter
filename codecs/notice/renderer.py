"""NapCat 通知文本渲染器。"""

from __future__ import annotations

from typing import Any, Mapping


class NapCatNoticeTextRenderer:
    """根据通知载荷生成可读文本。"""

    # 通知类型 → 简短标记 映射
    _EVENT_LABELS: dict = {
        ("notify", "poke"): "戳一戳",
        ("group_recall",): "撤回",
        ("friend_recall",): "撤回",
        ("notify", "group_name"): "群名变更",
        ("group_ban", "ban"): "禁言",
        ("group_ban", "whole_lift_ban"): "解除全体禁言",
        ("group_ban", "lift_ban"): "解除禁言",
        ("group_upload",): "文件上传",
        ("group_increase",): "成员加入",
        ("group_decrease",): "成员离开",
        ("group_admin",): "管理员变更",
        ("essence",): "精华消息",
        ("group_msg_emoji_like",): "表情回应",
    }

    @classmethod
    def _event_prefix(cls, notice_type: str, sub_type: str) -> str:
        """根据通知类型生成事件标记前缀。

        Args:
            notice_type: NapCat 通知类型。
            sub_type: NapCat 通知子类型。

        Returns:
            str: 事件标记，如 ``[事件-戳一戳]``。
        """
        label = cls._EVENT_LABELS.get((notice_type, sub_type)) or cls._EVENT_LABELS.get((notice_type,))
        if label:
            return f"[事件-{label}] "
        return "[事件] "

    def build_notice_text(
        self,
        payload: Mapping[str, Any],
        actor_name: str,
        target_name: str = "",
        recalled_content: str = "",
    ) -> str:
        """根据 NapCat 通知事件生成可读文本。

        Args:
            payload: 原始通知事件。
            actor_name: 事件操作者显示名（已格式化为 ``昵称[QQ号]``）。
            target_name: 事件目标显示名（已格式化为 ``昵称[QQ号]``）。
            recalled_content: 撤回事件中被撤回消息的原始文本内容。

        Returns:
            str: 生成的可读通知文本。
        """
        notice_type = str(payload.get("notice_type") or "").strip()
        sub_type = str(payload.get("sub_type") or "").strip()
        prefix = self._event_prefix(notice_type, sub_type)
        target_id = str(payload.get("target_id") or "").strip()
        target_user_id = str(payload.get("user_id") or "").strip()
        is_natural_lift = bool(payload.get("is_natural_lift", False))

        if notice_type in {"group_recall", "friend_recall"}:
            base = f"{actor_name} 撤回了 {target_name} 的消息" if target_name else f"{actor_name} 撤回了一条消息"
            if recalled_content:
                return f"{prefix}{base}：「{recalled_content}」"
            return f"{prefix}{base}"
        if notice_type == "notify" and sub_type == "poke":
            target_display = target_name or target_id
            target_text = f" -> {target_display}" if target_display else ""
            return f"{prefix}{actor_name} 发起了戳一戳{target_text}"
        if notice_type == "notify" and sub_type == "group_name":
            return f"{prefix}{actor_name} 修改了群名称"
        if notice_type == "group_ban" and sub_type == "ban":
            duration = payload.get("duration")
            target_display = target_name or target_user_id
            if target_user_id in {"", "0"}:
                return f"{prefix}{actor_name} 开启了全体禁言"
            return f"{prefix}{actor_name} 禁言了 {target_display}，时长 {duration} 秒"
        if notice_type == "group_ban" and sub_type == "whole_lift_ban":
            if is_natural_lift:
                return f"{prefix}群全体禁言已自然解除"
            return f"{prefix}{actor_name} 解除了全体禁言"
        if notice_type == "group_ban" and sub_type == "lift_ban":
            target_display = target_name or target_user_id
            if is_natural_lift:
                return f"{prefix}{target_display} 的禁言已自然解除"
            return f"{prefix}{actor_name} 解除了 {target_display} 的禁言"
        if notice_type == "group_upload":
            file_info = payload.get("file", {})
            file_name = ""
            file_size = ""
            if isinstance(file_info, Mapping):
                file_name = str(file_info.get("name") or "").strip()
                raw_size = file_info.get("size")
                if isinstance(raw_size, (int, float)) and raw_size > 0:
                    if raw_size >= 1048576:
                        file_size = f" ({raw_size / 1048576:.1f}MB)"
                    elif raw_size >= 1024:
                        file_size = f" ({raw_size / 1024:.0f}KB)"
                    else:
                        file_size = f" ({raw_size}B)"
            return f"{prefix}{actor_name} 上传了文件：{file_name}{file_size}" if file_name else f"{prefix}{actor_name} 上传了文件"
        if notice_type == "group_increase":
            target_display = target_name or f"用户{target_user_id}" if target_user_id else ""
            if target_display and actor_name != target_display:
                return f"{prefix}{actor_name} 邀请 {target_display} 加入了群聊"
            return f"{prefix}{target_display or actor_name} 加入了群聊"
        if notice_type == "group_decrease":
            target_display = target_name or f"用户{target_user_id}" if target_user_id else ""
            decrease_sub = str(payload.get("sub_type") or "").strip()
            if decrease_sub == "kick":
                if target_display and actor_name != target_display:
                    return f"{prefix}{actor_name} 将 {target_display} 移出群聊"
                return f"{prefix}{target_display or actor_name} 被移出群聊"
            if decrease_sub == "kick_me":
                return f"{prefix}{actor_name} 主动退出了群聊"
            if target_display and actor_name != target_display:
                return f"{prefix}{target_display} 离开了群聊（操作者：{actor_name}）"
            return f"{prefix}{actor_name} 离开了群聊"
        if notice_type == "group_admin":
            admin_sub = str(payload.get("sub_type") or "").strip()
            if admin_sub == "set":
                return f"{prefix}{actor_name} 被设置为管理员"
            if admin_sub == "unset":
                return f"{prefix}{actor_name} 被取消了管理员"
            return f"{prefix}{actor_name} 的群管理员状态发生变化"
        if notice_type == "essence":
            essence_sub = str(payload.get("sub_type") or "").strip()
            if essence_sub == "add":
                return f"{prefix}{actor_name} 设置了一条精华消息"
            if essence_sub == "delete":
                return f"{prefix}{actor_name} 取消了一条精华消息"
            return f"{prefix}{actor_name} 触发了精华消息事件"
        if notice_type == "group_msg_emoji_like":
            likes = payload.get("likes")
            if isinstance(likes, list) and likes:
                emoji_parts = []
                for like in likes:
                    if isinstance(like, dict):
                        eid = str(like.get("emoji_id") or "?")
                        cnt = like.get("count", 1)
                        emoji_parts.append(f"表情{eid}×{cnt}")
                emoji_detail = "、".join(emoji_parts) if emoji_parts else "表情"
            else:
                emoji_detail = "表情"
            return f"{prefix}{actor_name} 给一条消息添加了 {emoji_detail}"
        return f"{prefix}{notice_type}.{sub_type}".strip(".")
