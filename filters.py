"""NapCat 入站消息过滤。"""

from __future__ import annotations

import re
from typing import Any, Collection, List, Pattern

from .config import NapCatChatConfig, NapCatFilterConfig, NapCatNoticeConfig


class NapCatRegexFilter:
    """NapCat 正则表达式消息内容过滤器。

    通过配置的正则表达式列表对消息纯文本进行匹配，
    支持黑名单（匹配则丢弃）和白名单（仅放行匹配）两种模式。
    """

    def __init__(self, logger: Any) -> None:
        """初始化正则表达式过滤器。

        Args:
            logger: 插件日志对象。
        """
        self._logger = logger
        self._compiled_patterns: List[Pattern[str]] = []
        self._source_patterns: List[str] = []

    def reload_patterns(self, patterns: List[str]) -> None:
        """根据正则表达式列表重新编译。

        无效的正则表达式会被记录警告并跳过。

        Args:
            patterns: 正则表达式字符串列表。
        """
        compiled: List[Pattern[str]] = []
        source: List[str] = []
        for pattern_text in patterns:
            try:
                compiled.append(re.compile(pattern_text))
                source.append(pattern_text)
            except re.error as exc:
                self._logger.warning(f"NapCat 正则过滤器忽略无效正则表达式 '{pattern_text}': {exc}")
        self._compiled_patterns = compiled
        self._source_patterns = source
        self._logger.debug(
            f"NapCat 正则过滤器已加载 {len(compiled)} 条规则: {source}"
        )

    def is_message_allowed(self, plain_text: str, filter_config: NapCatFilterConfig) -> bool:
        """检查消息文本是否通过正则表达式过滤。

        Args:
            plain_text: 消息纯文本内容。
            filter_config: 当前生效的消息过滤配置。

        Returns:
            bool: 若消息允许继续进入 Host，则返回 ``True``。
        """
        if not filter_config.regex_filter_enabled:
            return True

        if not self._compiled_patterns:
            if filter_config.regex_filter_mode == "whitelist":
                self._log_regex_rejection(
                    filter_config.regex_filter_show_dropped,
                    "NapCat 白名单正则过滤器无有效规则，消息被丢弃",
                )
                return False
            return True

        matched = self._matches_any_pattern(plain_text)

        if filter_config.regex_filter_mode == "blacklist":
            # 黑名单模式：匹配则丢弃
            if matched:
                self._log_regex_rejection(
                    filter_config.regex_filter_show_dropped,
                    f"NapCat 消息匹配黑名单正则，消息被丢弃: {plain_text!r}",
                )
                return False
            return True

        # 白名单模式：不匹配则丢弃
        if not matched:
            self._log_regex_rejection(
                filter_config.regex_filter_show_dropped,
                f"NapCat 消息未匹配白名单正则，消息被丢弃: {plain_text!r}",
            )
            return False
        return True

    def _matches_any_pattern(self, text: str) -> bool:
        """判断文本是否匹配任意一条已编译的正则表达式。

        Args:
            text: 待匹配的文本。

        Returns:
            bool: 若匹配到任意一条正则，则返回 ``True``。
        """
        for pattern in self._compiled_patterns:
            if pattern.search(text):
                return True
        return False

    def _log_regex_rejection(self, enabled: bool, message: str) -> None:
        """按配置决定是否记录正则过滤丢弃日志。"""
        if enabled:
            self._logger.warning(message)


class NapCatChatFilter:
    """NapCat 聊天名单过滤器。"""

    def __init__(self, logger: Any) -> None:
        """初始化聊天名单过滤器。

        Args:
            logger: 插件日志对象。
        """
        self._logger = logger

    def is_inbound_chat_allowed(
        self,
        sender_user_id: str,
        group_id: str,
        chat_config: NapCatChatConfig,
    ) -> bool:
        """检查入站消息是否通过聊天名单过滤。

        Args:
            sender_user_id: 发送者用户 ID。
            group_id: 群聊 ID；私聊时为空字符串。
            chat_config: 当前生效的聊天配置。

        Returns:
            bool: 若消息允许继续进入 Host，则返回 ``True``。
        """
        if sender_user_id in chat_config.ban_user_id:
            self._logger.warning(f"NapCat 用户 {sender_user_id} 在全局禁止名单中，消息被丢弃")
            return False

        if not chat_config.enable_chat_list_filter:
            return True

        if group_id:
            if not self._is_id_allowed_by_list_policy(group_id, chat_config.group_list_type, chat_config.group_list):
                self._log_chat_list_rejection(
                    chat_config.show_dropped_chat_list_messages,
                    f"NapCat 群聊 {group_id} 未通过聊天名单过滤，消息被丢弃",
                )
                return False
            return True

        if not self._is_id_allowed_by_list_policy(
            sender_user_id,
            chat_config.private_list_type,
            chat_config.private_list,
        ):
            self._log_chat_list_rejection(
                chat_config.show_dropped_chat_list_messages,
                f"NapCat 私聊用户 {sender_user_id} 未通过聊天名单过滤，消息被丢弃",
            )
            return False
        return True

    def _log_chat_list_rejection(self, enabled: bool, message: str) -> None:
        """按配置决定是否记录聊天名单过滤丢弃日志。"""
        if enabled:
            self._logger.warning(message)

    @staticmethod
    def _is_id_allowed_by_list_policy(target_id: str, list_type: str, configured_ids: Collection[str]) -> bool:
        """根据白名单或黑名单规则判断目标 ID 是否允许通过。

        Args:
            target_id: 待检查的目标 ID。
            list_type: 名单模式，仅支持 ``whitelist`` 或 ``blacklist``。
            configured_ids: 配置中的 ID 集合或列表。

        Returns:
            bool: 若目标 ID 允许通过，则返回 ``True``。
        """
        if list_type == "whitelist":
            return target_id in configured_ids
        return target_id not in configured_ids


class NapCatNoticeFilter:
    """NapCat 通知事件类型过滤器。

    依据通知事件配置，按白名单方式决定某类通知是否允许注入 Host：
    仅明确启用的类型放行，未列出的类型（如输入状态 ``notify.input_status``）默认丢弃。
    """

    _NOTICE_TYPE_FIELDS = {
        "friend_recall": "enable_friend_recall",
        "group_recall": "enable_group_recall",
        "group_ban": "enable_group_ban",
        "group_msg_emoji_like": "enable_group_msg_emoji_like",
        "group_upload": "enable_group_upload",
        "group_increase": "enable_group_increase",
        "group_decrease": "enable_group_decrease",
        "group_admin": "enable_group_admin",
        "essence": "enable_essence",
    }

    def __init__(self, logger: Any) -> None:
        """初始化通知事件过滤器。

        Args:
            logger: 插件日志对象。
        """
        self._logger = logger

    def is_notice_event_allowed(
        self,
        notice_type: str,
        sub_type: str,
        notice_config: NapCatNoticeConfig,
    ) -> bool:
        """判断指定通知事件是否允许注入 Host。

        Args:
            notice_type: 通知事件主类型。
            sub_type: 通知事件子类型；无子类型时为空字符串。
            notice_config: 当前生效的通知事件配置。

        Returns:
            bool: 若该通知允许继续进入 Host，则返回 ``True``。
        """
        if not notice_config.enabled:
            return False

        if notice_type == "notify":
            if sub_type == "poke":
                return notice_config.enable_poke
            if sub_type == "group_name":
                return notice_config.enable_group_name
            # notify 下的其它子类型（如输入状态 input_status）默认不传递
            return False

        field_name = self._NOTICE_TYPE_FIELDS.get(notice_type)
        if field_name is None:
            # 未在配置中列出的通知类型默认不传递
            return False
        return bool(getattr(notice_config, field_name))
