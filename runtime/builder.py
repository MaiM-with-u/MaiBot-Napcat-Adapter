"""NapCat 运行时组件构建器。"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Coroutine

from ..codecs.inbound import NapCatInboundCodec
from ..codecs.notice import NapCatNoticeCodec
from ..codecs.outbound import NapCatOutboundCodec
from ..filters import NapCatChatFilter, NapCatNoticeFilter, NapCatRegexFilter
from ..heartbeat_monitor import NapCatHeartbeatMonitor
from ..runtime_state import NapCatRuntimeStateManager
from ..services import (
    NapCatActionService,
    NapCatBanStateStore,
    NapCatBanTracker,
    NapCatOfficialBotGuard,
    NapCatQueryService,
)
from ..transport import NapCatTransportClient
from .bundle import NapCatRuntimeBundle


class NapCatRuntimeBuilder:
    """按固定依赖图构建 NapCat 运行时组件。"""

    def __init__(self, gateway_capability: Any, logger: Any, gateway_name: str) -> None:
        """初始化运行时构建器。

        Args:
            gateway_capability: SDK 提供的消息网关能力对象。
            logger: 插件日志对象。
            gateway_name: 当前消息网关名称。
        """
        self._gateway_capability = gateway_capability
        self._logger = logger
        self._gateway_name = gateway_name

    def build(
        self,
        on_connection_opened: Callable[[], Coroutine[Any, Any, None]],
        on_connection_closed: Callable[[], Coroutine[Any, Any, None]],
        on_payload: Callable[[dict[str, Any]], Coroutine[Any, Any, None]],
        on_natural_lift: Callable[[dict[str, Any]], Awaitable[None]],
        on_heartbeat_timeout: Callable[[str], Awaitable[None]],
    ) -> NapCatRuntimeBundle:
        """创建一套完整的运行时组件。

        Args:
            on_connection_opened: 连接建立回调。
            on_connection_closed: 连接断开回调。
            on_payload: 非 echo 载荷回调。
            on_natural_lift: 自然解除禁言回调。
            on_heartbeat_timeout: 心跳超时回调。

        Returns:
            NapCatRuntimeBundle: 已完成依赖注入的运行时组件集合。
        """
        chat_filter = NapCatChatFilter(self._logger)
        notice_filter = NapCatNoticeFilter(self._logger)
        regex_filter = NapCatRegexFilter(self._logger)
        transport = NapCatTransportClient(
            logger=self._logger,
            on_connection_opened=on_connection_opened,
            on_connection_closed=on_connection_closed,
            on_payload=on_payload,
        )
        action_service = NapCatActionService(self._logger, transport)
        query_service = NapCatQueryService(action_service, self._logger)
        ban_state_store = NapCatBanStateStore(self._logger)
        inbound_codec = NapCatInboundCodec(self._logger, query_service)
        notice_codec = NapCatNoticeCodec(self._logger, query_service)
        runtime_state = NapCatRuntimeStateManager(
            gateway_capability=self._gateway_capability,
            logger=self._logger,
            gateway_name=self._gateway_name,
        )
        ban_tracker = NapCatBanTracker(
            logger=self._logger,
            query_service=query_service,
            on_natural_lift=on_natural_lift,
            state_store=ban_state_store,
        )
        heartbeat_monitor = NapCatHeartbeatMonitor(
            logger=self._logger,
            on_timeout=on_heartbeat_timeout,
        )
        official_bot_guard = NapCatOfficialBotGuard(self._logger, query_service)
        outbound_codec = NapCatOutboundCodec()

        return NapCatRuntimeBundle(
            action_service=action_service,
            ban_state_store=ban_state_store,
            ban_tracker=ban_tracker,
            chat_filter=chat_filter,
            heartbeat_monitor=heartbeat_monitor,
            inbound_codec=inbound_codec,
            notice_codec=notice_codec,
            notice_filter=notice_filter,
            official_bot_guard=official_bot_guard,
            outbound_codec=outbound_codec,
            query_service=query_service,
            regex_filter=regex_filter,
            runtime_state=runtime_state,
            transport=transport,
        )
