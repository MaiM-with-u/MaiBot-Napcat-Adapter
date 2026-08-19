"""NapCat 事件路由协调器。"""

from __future__ import annotations

from typing import Any, Callable, Dict, Mapping, Optional, Protocol

import asyncio

from ..codecs.notice.helpers import resolve_actor_user_id
from ..config import NapCatPluginSettings
from ..types import NapCatPayloadDict
from .bundle import NapCatRuntimeBundle
from ..codecs.notice.helpers import resolve_actor_user_id


class _GatewayCapabilityProtocol(Protocol):
    """插件网关能力协议。"""

    async def route_message(
        self,
        gateway_name: str,
        message: Dict[str, Any],
        *,
        route_metadata: Optional[Dict[str, Any]] = None,
        external_message_id: str = "",
        dedupe_key: str = "",
    ) -> bool:
        """向 Host 注入一条消息。"""
        ...


class NapCatEventRouter:
    """协调 NapCat 运行时组件处理各类平台事件。"""

    def __init__(
        self,
        gateway_capability: _GatewayCapabilityProtocol,
        logger: Any,
        gateway_name: str,
        load_settings: Callable[[], NapCatPluginSettings],
    ) -> None:
        """初始化事件路由器。

        Args:
            gateway_capability: SDK 提供的消息网关能力对象。
            logger: 插件日志对象。
            gateway_name: 当前消息网关名称。
            load_settings: 返回当前生效插件配置的回调。
        """
        self._gateway_capability = gateway_capability
        self._logger = logger
        self._gateway_name = gateway_name
        self._load_settings = load_settings
        self._runtime: Optional[NapCatRuntimeBundle] = None

    def bind_runtime(self, runtime: NapCatRuntimeBundle) -> None:
        """绑定当前路由器使用的运行时依赖。

        Args:
            runtime: 已初始化的运行时组件集合。
        """
        self._runtime = runtime

    def reset_caches(self) -> None:
        """重置与路由相关的短期缓存。"""
        runtime = self._runtime
        if runtime is None:
            return
        runtime.official_bot_guard.clear_cache()

    async def handle_transport_payload(self, payload: NapCatPayloadDict) -> None:
        """处理来自传输层的非 echo 载荷。

        Args:
            payload: NapCat 推送的原始事件数据。
        """
        post_type = str(payload.get("post_type") or "").strip()
        if post_type == "message":
            await self.handle_inbound_message(payload)
            return
        if post_type == "notice":
            await self.handle_notice_event(payload)
            return
        if post_type == "meta_event":
            await self.handle_meta_event(payload)

    async def handle_inbound_message(self, payload: NapCatPayloadDict) -> None:
        """处理单条 NapCat 入站消息并注入 Host。

        Args:
            payload: NapCat / OneBot 推送的原始消息事件。
        """
        runtime = self._require_runtime()
        settings = self._load_settings()

        self_id = str(payload.get("self_id") or "").strip()
        if self_id:
            await runtime.runtime_state.report_connected(self_id, settings.napcat_server)

        sender = payload.get("sender", {})
        if not isinstance(sender, Mapping):
            sender = {}

        sender_user_id = str(payload.get("user_id") or sender.get("user_id") or "").strip()
        if not sender_user_id:
            return

        group_id = str(payload.get("group_id") or "").strip()
        if self_id and sender_user_id == self_id and settings.filters.ignore_self_message:
            return
        if not runtime.chat_filter.is_inbound_chat_allowed(sender_user_id, group_id, settings.chat):
            return
        if await runtime.official_bot_guard.should_reject(
            sender_user_id=sender_user_id,
            group_id=group_id,
            ban_qq_bot=settings.chat.ban_qq_bot,
        ):
            return

        try:
            message_dict = await runtime.inbound_codec.build_message_dict(payload, self_id, sender_user_id, sender)
        except ValueError as exc:
            self._logger.warning(f"NapCat 入站消息格式不受支持，已丢弃: {exc}")
            return

        plain_text = str(message_dict.get("processed_plain_text") or "").strip()
        if not runtime.regex_filter.is_message_allowed(plain_text, settings.filters):
            return

        route_metadata = self._build_route_metadata(self_id, settings.napcat_server.connection_id)
        external_message_id = str(payload.get("message_id") or "").strip()
        accepted = await self._gateway_capability.route_message(
            gateway_name=self._gateway_name,
            message=message_dict,
            route_metadata=route_metadata,
            external_message_id=external_message_id,
            dedupe_key=external_message_id,
        )
        if not accepted:
            self._logger.debug(f"Host 丢弃了 NapCat 入站消息: {external_message_id or '无消息 ID'}")

    async def handle_notice_event(self, payload: NapCatPayloadDict) -> None:
        """处理 NapCat ``notice`` 事件并注入 Host。

        遵循与普通消息一致的全局配置过滤规则（白名单/黑名单、禁止用户等）。

        Args:
            payload: NapCat 推送的通知事件。
        """
        runtime = self._require_runtime()
        settings = self._load_settings()

        self_id = str(payload.get("self_id") or "").strip()
        if self_id:
            await runtime.runtime_state.report_connected(self_id, settings.napcat_server)

        # 应用全局过滤配置
        notice_user_id = resolve_actor_user_id(payload)
        group_id = str(payload.get("group_id") or "").strip()
        # 若通知的目标用户是机器人自身（如被设置/取消管理员），
        # 则跳过 ignore_self_message 检查，确保机器人感知到针对自己的操作。
        target_user_id = str(payload.get("user_id") or "").strip()
        is_targeting_self = bool(self_id and target_user_id == self_id)
        if not is_targeting_self and self_id and notice_user_id == self_id and settings.filters.ignore_self_message:
            return
        if not runtime.chat_filter.is_inbound_chat_allowed(notice_user_id, group_id, settings.chat):
            return
        if await runtime.official_bot_guard.should_reject(
            sender_user_id=notice_user_id,
            group_id=group_id,
            ban_qq_bot=settings.chat.ban_qq_bot,
        ):
            return

        await runtime.ban_tracker.record_notice(payload)
        await self.route_notice_payload(payload, self_id, settings.napcat_server.connection_id)

    async def route_notice_payload(
        self,
        payload: NapCatPayloadDict,
        self_id: str,
        connection_id: str,
    ) -> None:
        """将单条通知载荷转换并注入 Host。

        注入前按与普通消息一致的口径执行聊天名单过滤，
        避免非名单内群聊/私聊的通知事件（如戳一戳）泄漏进 Host。

        Args:
            payload: NapCat 通知载荷。
            self_id: 当前机器人账号 ID。
            connection_id: 当前连接标识。
        """
        runtime = self._require_runtime()
        settings = self._load_settings()

        notice_type = str(payload.get("notice_type") or "").strip()
        sub_type = str(payload.get("sub_type") or "").strip()
        if not runtime.notice_filter.is_notice_event_allowed(notice_type, sub_type, settings.notice):
            self._logger.debug(
                f"NapCat 通知事件未启用，已丢弃: {notice_type}.{sub_type or '<空>'}"
            )
            return

        group_id = str(payload.get("group_id") or "").strip()
        actor_user_id = resolve_actor_user_id(payload)
        # 群/私聊至少能确定其一时才做名单过滤；两者皆空的通知无法归类，保持放行
        if (group_id or actor_user_id) and not runtime.chat_filter.is_inbound_chat_allowed(
            actor_user_id, group_id, settings.chat
        ):
            return

        message_dict = await runtime.notice_codec.build_notice_message_dict(payload)
        if message_dict is None:
            return

        route_metadata = self._build_route_metadata(self_id, connection_id)
        external_message_id = str(payload.get("message_id") or "").strip()
        dedupe_key = runtime.notice_codec.build_notice_dedupe_key(payload) or ""
        accepted = await self._gateway_capability.route_message(
            gateway_name=self._gateway_name,
            message=message_dict,
            route_metadata=route_metadata,
            external_message_id=external_message_id,
            dedupe_key=dedupe_key,
        )
        if not accepted:
            self._logger.debug(f"Host 丢弃了 NapCat 通知事件: {external_message_id or dedupe_key or '无消息 ID'}")

    async def emit_natural_lift_notice(self, payload: NapCatPayloadDict) -> None:
        """注入一条由适配器合成的自然解除禁言通知。

        Args:
            payload: 合成后的 NapCat 通知载荷。
        """
        settings = self._load_settings()
        self_id = str(payload.get("self_id") or "").strip()
        await self.route_notice_payload(payload, self_id, settings.napcat_server.connection_id)

    async def handle_meta_event(self, payload: NapCatPayloadDict) -> None:
        """处理 NapCat ``meta_event`` 事件。

        Args:
            payload: NapCat 推送的元事件。
        """
        runtime = self._require_runtime()
        settings = self._load_settings()

        meta_event_type = str(payload.get("meta_event_type") or "").strip()
        self_id = str(payload.get("self_id") or "").strip()
        should_report_connected = False
        if meta_event_type == "lifecycle":
            should_report_connected = str(payload.get("sub_type") or "").strip() == "connect"
        elif meta_event_type == "heartbeat":
            status = payload.get("status", {})
            if not isinstance(status, Mapping):
                status = {}
            should_report_connected = bool(status.get("online", False)) and bool(status.get("good", False))

        if self_id and should_report_connected:
            await runtime.runtime_state.report_connected(self_id, settings.napcat_server)
        elif meta_event_type == "heartbeat" and not should_report_connected:
            await runtime.runtime_state.report_disconnected()

        await runtime.heartbeat_monitor.observe_meta_event(payload, settings.napcat_server.heartbeat_interval)
        await runtime.notice_codec.handle_meta_event(payload)

    async def bootstrap_adapter_runtime_state(self) -> None:
        """在连接建立后主动获取账号信息并激活消息网关路由。"""
        runtime = self._require_runtime()
        settings = self._load_settings()

        max_attempts = 3
        last_error: Optional[Exception] = None
        for attempt in range(1, max_attempts + 1):
            try:
                login_info = await runtime.query_service.get_login_info()
                self_id = self._extract_self_id_from_login_response(login_info)
                await runtime.runtime_state.report_connected(self_id, settings.napcat_server)
                await runtime.heartbeat_monitor.start(self_id, settings.napcat_server.heartbeat_interval)
                await runtime.ban_tracker.start()
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                last_error = exc
                self._logger.warning(f"NapCat 消息网关获取登录信息失败，第 {attempt}/{max_attempts} 次重试: {exc}")
                if attempt < max_attempts:
                    await asyncio.sleep(1.0)

        if last_error is not None:
            self._logger.error(f"NapCat 消息网关未能完成路由激活，连接将保持只接收状态: {last_error}")

    async def handle_transport_disconnected(self) -> None:
        """处理传输层断开事件。"""
        runtime = self._require_runtime()
        await runtime.heartbeat_monitor.stop()
        await runtime.ban_tracker.stop()
        self.reset_caches()
        await runtime.runtime_state.report_disconnected()

    async def handle_heartbeat_timeout(self, self_id: str) -> None:
        """处理 NapCat 心跳长时间未更新的情况。

        Args:
            self_id: 当前机器人账号 ID。
        """
        runtime = self._require_runtime()
        if self_id:
            self._logger.warning(f"NapCat Bot {self_id} 心跳超时，暂时将消息网关标记为未就绪")
        else:
            self._logger.warning("NapCat 心跳超时，暂时将消息网关标记为未就绪")
        await runtime.runtime_state.report_disconnected()

    def _require_runtime(self) -> NapCatRuntimeBundle:
        """返回当前已绑定的运行时依赖。

        Returns:
            NapCatRuntimeBundle: 已初始化的运行时依赖。

        Raises:
            RuntimeError: 当运行时尚未绑定时抛出。
        """
        runtime = self._runtime
        if runtime is None:
            raise RuntimeError("NapCat 运行时尚未初始化")
        return runtime

    @staticmethod
    def _build_route_metadata(self_id: str, connection_id: str) -> Dict[str, Any]:
        """构造注入 Host 时使用的路由元数据。

        Args:
            self_id: 当前机器人账号 ID。
            connection_id: 当前连接标识。

        Returns:
            Dict[str, Any]: 路由元数据字典。
        """
        route_metadata: Dict[str, Any] = {}
        if self_id:
            route_metadata["self_id"] = self_id
        if connection_id:
            route_metadata["connection_id"] = connection_id
        return route_metadata

    @staticmethod
    def _extract_self_id_from_login_response(response: Optional[Dict[str, Any]]) -> str:
        """从 ``get_login_info`` 查询结果中提取当前账号 ID。

        Args:
            response: NapCat 返回的登录信息字典。

        Returns:
            str: 规范化后的账号 ID 字符串。

        Raises:
            ValueError: 当响应中缺少有效账号 ID 时抛出。
        """
        if not isinstance(response, Mapping):
            raise ValueError("get_login_info 响应缺少 data 字段")

        self_id = str(response.get("user_id") or "").strip()
        if not self_id:
            raise ValueError("get_login_info 响应缺少有效的 user_id")
        return self_id
