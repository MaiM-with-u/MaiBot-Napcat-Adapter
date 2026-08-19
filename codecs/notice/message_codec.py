"""NapCat 通知事件编解码器。"""

from __future__ import annotations

from typing import Any, Dict, Optional
from uuid import uuid4

import time

from ...services import NapCatQueryService
from ...types import NapCatPayload, NapCatPayloadDict
from .enricher import NapCatNoticeEntityResolver
from .helpers import build_payload_digest, resolve_actor_user_id
from .meta_event_logger import NapCatMetaEventObserver
from .renderer import NapCatNoticeTextRenderer


class NapCatNoticeCodec:
    """NapCat QQ 通知事件编码器。"""

    def __init__(self, logger: Any, query_service: NapCatQueryService) -> None:
        """初始化通知事件编码器。

        Args:
            logger: 插件日志对象。
            query_service: QQ 查询服务。
        """
        self._entity_resolver = NapCatNoticeEntityResolver(query_service)
        self._query_service = query_service
        self._logger = logger
        self._meta_event_observer = NapCatMetaEventObserver(logger)
        self._renderer = NapCatNoticeTextRenderer()

    async def build_notice_message_dict(self, payload: NapCatPayload) -> Optional[NapCatPayloadDict]:
        """将 NapCat ``notice`` 事件转换为 Host 可接受的消息字典。

        Args:
            payload: NapCat 推送的原始通知事件。

        Returns:
            Optional[NapCatPayloadDict]: 成功时返回标准 ``MessageDict``；无法识别时返回 ``None``。
        """
        notice_type = str(payload.get("notice_type") or "").strip()
        if not notice_type:
            return None

        group_id = str(payload.get("group_id") or "").strip()
        user_id = resolve_actor_user_id(payload)
        self_id = str(payload.get("self_id") or "").strip()

        user_info = await self._entity_resolver.build_user_info(group_id=group_id, user_id=user_id)
        group_info = await self._entity_resolver.build_group_info(group_id)
        actor_name = self._entity_resolver._format_display_name(user_info, user_id or "系统")

        # 解析事件目标用户及撤回消息内容
        target_name = ""
        recalled_content = ""
        sub_type = str(payload.get("sub_type") or "").strip()
        if notice_type == "notify" and sub_type == "poke":
            target_id = str(payload.get("target_id") or "").strip()
            if target_id:
                target_name = await self._entity_resolver.resolve_target_display_name(group_id, target_id)
        elif notice_type in {"group_recall", "friend_recall"}:
            # 撤回事件：payload.user_id 是被撤回消息的原发送者
            original_sender_id = str(payload.get("user_id") or "").strip()
            if original_sender_id and original_sender_id != user_id:
                target_name = await self._entity_resolver.resolve_target_display_name(group_id, original_sender_id)
            # 尝试获取被撤回消息的内容
            recalled_msg_id = str(payload.get("message_id") or "").strip()
            if recalled_msg_id:
                try:
                    msg_detail = await self._query_service.get_message_detail(recalled_msg_id)
                    if isinstance(msg_detail, dict):
                        raw = msg_detail.get("raw_message", "")
                        msg_text = msg_detail.get("message", "")
                        recalled_content = str(raw or msg_text or "").strip()
                except Exception:
                    pass
        elif notice_type == "group_ban" and sub_type in {"ban", "lift_ban"}:
            # 禁言/解除禁言：payload.user_id 是被操作的目标用户
            target_user_id = str(payload.get("user_id") or "").strip()
            if target_user_id and target_user_id not in {"", "0"}:
                target_name = await self._entity_resolver.resolve_target_display_name(group_id, target_user_id)
        elif notice_type in {"group_increase", "group_decrease"}:
            # 加入/离开事件：payload.user_id 是加入/离开的那个人（被操作对象）
            # resolve_actor_user_id 已正确处理：actor=operator_id, target=user_id
            target_user_id = str(payload.get("user_id") or "").strip()
            if target_user_id and target_user_id != user_id:
                target_name = await self._entity_resolver.resolve_target_display_name(group_id, target_user_id)

        notice_text = self._renderer.build_notice_text(
            payload, actor_name, target_name=target_name, recalled_content=recalled_content
        )
        if not notice_text:
            return None

        additional_config: Dict[str, Any] = {
            "self_id": self_id,
            "napcat_notice_type": notice_type,
            "napcat_notice_sub_type": str(payload.get("sub_type") or "").strip(),
            "napcat_notice_payload": dict(payload),
        }
        if group_id:
            additional_config["platform_io_target_group_id"] = group_id
        elif user_id:
            additional_config["platform_io_target_user_id"] = user_id

        message_info: Dict[str, Any] = {"user_info": user_info, "additional_config": additional_config}
        if group_info is not None:
            message_info["group_info"] = group_info

        timestamp_seconds = payload.get("time")
        if not isinstance(timestamp_seconds, (int, float)):
            timestamp_seconds = time.time()

        return {
            "message_id": f"napcat-notice-{uuid4().hex}",
            "timestamp": str(float(timestamp_seconds)),
            "platform": "qq",
            "message_info": message_info,
            "raw_message": [{"type": "text", "data": notice_text}],
            "is_mentioned": False,
            "is_at": False,
            "is_emoji": False,
            "is_picture": False,
            "is_command": False,
            "is_notify": True,
            "session_id": "",
            "processed_plain_text": notice_text,
            "display_message": notice_text,
        }

    def build_notice_dedupe_key(self, payload: NapCatPayload) -> Optional[str]:
        """为 NapCat ``notice`` 事件构造稳定的技术性去重键。

        注意：通知事件中的 ``message_id`` 可能与被引用的原始消息 ID 相同
        （如撤回事件），因此必须加上事件类型前缀以避免与普通消息的去重键冲突。

        Args:
            payload: NapCat 推送的原始通知事件。

        Returns:
            Optional[str]: 若可以构造稳定去重键则返回该键，否则返回 ``None``。
        """
        notice_type = str(payload.get("notice_type") or "").strip()
        sub_type = str(payload.get("sub_type") or "").strip()
        external_message_id = str(payload.get("message_id") or "").strip()
        if external_message_id:
            return f"notice:{notice_type}:{sub_type}:{external_message_id}"

        if not notice_type:
            return None

        suffix = f":{sub_type}" if sub_type else ""
        payload_digest = build_payload_digest(payload)
        return f"notice:{notice_type}{suffix}:{payload_digest}"

    async def handle_meta_event(self, payload: NapCatPayload) -> None:
        """处理 ``meta_event`` 事件的日志与状态观测。

        Args:
            payload: NapCat 推送的原始元事件。
        """
        await self._meta_event_observer.handle_meta_event(payload)
