"""NapCat 通知事件资料补全器。"""

from __future__ import annotations

from typing import Any, Dict, Optional

from ...services import NapCatQueryService
from .helpers import normalize_optional_string


class NapCatNoticeEntityResolver:
    """为通知事件补全用户和群资料。"""

    def __init__(self, query_service: NapCatQueryService) -> None:
        """初始化实体补全器。

        Args:
            query_service: NapCat 查询服务。
        """
        self._query_service = query_service

    async def build_user_info(self, group_id: str, user_id: str) -> Dict[str, Optional[str]]:
        """构造通知消息的用户信息。

        Args:
            group_id: 群号；私聊或系统通知时为空字符串。
            user_id: 事件关联用户号。

        Returns:
            Dict[str, Optional[str]]: 规范化后的用户信息字典。
        """
        if not user_id:
            return {
                "user_id": "notice",
                "user_nickname": "系统通知",
                "user_cardname": None,
            }

        member_info: Optional[Dict[str, Any]]
        if group_id:
            member_info = await self._query_service.get_group_member_info(group_id, user_id)
        else:
            member_info = await self._query_service.get_stranger_info(user_id)

        if member_info is None:
            return {
                "user_id": user_id,
                "user_nickname": user_id,
                "user_cardname": None,
            }

        return {
            "user_id": user_id,
            "user_nickname": str(member_info.get("nickname") or user_id),
            "user_cardname": normalize_optional_string(member_info.get("card")),
        }

    async def resolve_target_display_name(self, group_id: str, target_id: str) -> str:
        """解析目标用户的显示名称，格式为 ``昵称[QQ号]``。

        优先使用群名片，其次使用昵称，均不可用时仅返回 QQ 号。

        Args:
            group_id: 群号。
            target_id: 目标用户 QQ 号。

        Returns:
            str: 格式化后的显示名称，如 ``小明[123456]``。
        """
        if not target_id:
            return target_id

        user_info = await self.build_user_info(group_id=group_id, user_id=target_id)
        return self._format_display_name(user_info, target_id)

    @staticmethod
    def _format_display_name(user_info: Dict[str, Optional[str]], fallback_id: str) -> str:
        """从用户信息字典中提取显示名称，格式为 ``昵称[QQ号]``。

        Args:
            user_info: 由 ``build_user_info`` 返回的用户信息字典。
            fallback_id: 当信息不足时的回退 ID。

        Returns:
            str: 格式化后的显示名称。
        """
        nickname = user_info.get("user_nickname") or fallback_id
        cardname = user_info.get("user_cardname")
        display = cardname or nickname
        user_id = user_info.get("user_id") or fallback_id
        return f"{display}[{user_id}]"

    async def build_group_info(self, group_id: str) -> Optional[Dict[str, str]]:
        """构造通知消息的群信息。

        Args:
            group_id: 群号。

        Returns:
            Optional[Dict[str, str]]: 群信息字典；若不是群通知则返回 ``None``。
        """
        if not group_id:
            return None

        group_info = await self._query_service.get_group_info(group_id)
        group_name = str(group_info.get("group_name") or f"group_{group_id}") if group_info else f"group_{group_id}"
        return {"group_id": group_id, "group_name": group_name}
