"""内置 NapCat 适配器插件。

当前实现承担完整的 QQ / NapCat 消息网关职责：
1. 通过 WebSocket client/server 模式接入 NapCat / OneBot v11 服务。
2. 将入站消息、通知事件与元事件转换为 Host 侧结构。
3. 将 Host 出站消息转换为 OneBot 动作并发送。
4. 通过公开 API 暴露 QQ 平台专属查询与管理动作。
"""

from __future__ import annotations

from typing import Any, ClassVar, Dict, Mapping, Optional, cast

from maibot_sdk import MaiBotPlugin, MessageGateway, PluginConfigBase, Tool
from maibot_sdk.types import ToolParameterInfo, ToolParamType

from .apis import (
    NapCatAccountApiMixin,
    NapCatFileApiMixin,
    NapCatGroupApiMixin,
    NapCatMessageApiMixin,
    NapCatSystemApiMixin,
)
from .config import NapCatPluginSettings
from .constants import NAPCAT_GATEWAY_NAME, PRIVATE_CHAT_TOOL_BYPASS_SECONDS
from .runtime import NapCatEventRouter, NapCatRuntimeBuilder, NapCatRuntimeBundle
from .services import NapCatActionService, NapCatQueryService


class NapCatAdapterPlugin(
    NapCatAccountApiMixin,
    NapCatFileApiMixin,
    NapCatGroupApiMixin,
    NapCatMessageApiMixin,
    NapCatSystemApiMixin,
    MaiBotPlugin,
):
    """NapCat 消息网关与 QQ 能力插件。"""

    config_model: ClassVar[type[PluginConfigBase] | None] = NapCatPluginSettings

    def __init__(self) -> None:
        """初始化 NapCat 适配器插件实例。"""
        super().__init__()
        self._action_service: Optional[NapCatActionService] = None
        self._query_service: Optional[NapCatQueryService] = None
        self._event_router: Optional[NapCatEventRouter] = None
        self._runtime_bundle: Optional[NapCatRuntimeBundle] = None

    async def on_load(self) -> None:
        """在插件加载时根据配置决定是否启动连接。"""
        await self._sync_private_chat_tool_component_state()
        await self._restart_connection_if_needed()

    async def on_unload(self) -> None:
        """在插件卸载时关闭连接。"""
        await self._stop_connection()

    async def on_config_update(self, scope: str, config_data: Dict[str, Any], version: str) -> None:
        """在配置更新后重载连接状态。

        Args:
            scope: 配置变更范围。
            config_data: 最新的配置数据。
            version: 配置版本号。
        """
        if scope != "self":
            return

        self.set_plugin_config(config_data)
        if version:
            self.ctx.logger.debug(f"NapCat 适配器收到配置更新通知: {version}")
        await self._sync_private_chat_tool_component_state()
        await self._restart_connection_if_needed()

    @Tool(
        "open_private_chat",
        description=(
            "向指定 QQ 用户发送一条私聊消息，用于主动开启私聊。"
            "发送成功后，该用户在 15 分钟内的私聊入站消息会绕过私聊黑白名单过滤。"
        ),
        parameters=[
            ToolParameterInfo(
                name="user_id",
                param_type=ToolParamType.STRING,
                description="要开启私聊的 QQ 用户 ID，必须是正整数。",
                required=True,
            ),
            ToolParameterInfo(
                name="message",
                param_type=ToolParamType.STRING,
                description="要发送给该用户的第一条私聊消息。",
                required=True,
            ),
        ],
        enabled=False,
        visibility="visible",
    )
    async def tool_open_private_chat(
        self,
        user_id: Any = "",
        message: str = "",
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """主动向指定用户发送私聊消息，并临时放行该私聊。"""
        del kwargs

        try:
            normalized_user_id = str(self._normalize_positive_int(user_id, "user_id"))
        except ValueError as exc:
            return {"success": False, "error": str(exc)}

        normalized_message = str(message or "").strip()
        if not normalized_message:
            return {"success": False, "error": "私聊消息不能为空"}

        runtime_bundle = self._require_runtime_bundle()
        login_info = await runtime_bundle.query_service.get_login_info()
        account_id = str((login_info or {}).get("user_id") or "").strip()
        if not account_id:
            return {"success": False, "error": "无法获取当前 NapCat 登录账号"}

        settings = self._load_settings()
        open_session_result = await self.ctx.chat.open_session(
            platform="qq",
            chat_type="private",
            user_id=normalized_user_id,
            account_id=account_id,
            scope=settings.napcat_server.connection_id,
        )
        if not isinstance(open_session_result, Mapping) or not bool(open_session_result.get("success", False)):
            error = str(open_session_result.get("error") or "").strip() if isinstance(open_session_result, Mapping) else ""
            return {
                "success": False,
                "error": error or "打开私聊会话失败",
                "open_session_result": open_session_result,
            }

        try:
            response = await runtime_bundle.action_service.call_action(
                "send_private_msg",
                {
                    "user_id": int(normalized_user_id),
                    "message": [{"type": "text", "data": {"text": normalized_message}}],
                },
            )
        except Exception as exc:
            return {"success": False, "error": str(exc)}

        expires_at = runtime_bundle.chat_filter.grant_private_chat_bypass(normalized_user_id)
        response_data = response.get("data", {})
        message_id = str(response_data.get("message_id") or "") if isinstance(response_data, Mapping) else ""
        self.ctx.logger.info(f"NapCat 已主动开启私聊: user_id={normalized_user_id} message_id={message_id or '<unknown>'}")
        return {
            "success": True,
            "content": f"已向用户 {normalized_user_id} 发送私聊消息，并在 15 分钟内临时放行该私聊。",
            "user_id": normalized_user_id,
            "stream_id": str(open_session_result.get("session_id") or open_session_result.get("stream_id") or ""),
            "session": open_session_result.get("stream") or {},
            "message_id": message_id,
            "expires_at": expires_at,
            "bypass_seconds": PRIVATE_CHAT_TOOL_BYPASS_SECONDS,
        }

    @Tool(
        "get_qq_by_msg_id",
        description="根据当前聊天中的消息 ID 获取该消息发送者的 QQ 用户 ID。",
        parameters=[
            ToolParameterInfo(
                name="msg_id",
                param_type=ToolParamType.STRING,
                description="目标用户发送的消息 ID。",
                required=True,
            ),
        ],
        enabled=False,
        visibility="visible",
    )
    async def tool_get_qq_by_msg_id(
        self,
        msg_id: str = "",
        stream_id: str = "",
        chat_id: str = "",
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """根据消息 ID 查询该消息发送者的 QQ 号。"""
        del kwargs

        normalized_msg_id = str(msg_id or "").strip()
        if not normalized_msg_id:
            return {"success": False, "error": "缺少目标消息 ID"}

        target_stream_id = str(stream_id or chat_id or "").strip()
        query_result = await self.ctx.message.get_by_id(
            normalized_msg_id,
            stream_id=target_stream_id,
            include_binary_data=False,
        )
        if not isinstance(query_result, Mapping):
            return {"success": False, "error": f"未找到消息: {normalized_msg_id}", "msg_id": normalized_msg_id}

        message_info = query_result.get("message_info", {})
        user_info = message_info.get("user_info", {}) if isinstance(message_info, Mapping) else {}
        user_info = user_info if isinstance(user_info, Mapping) else {}
        user_id = str(user_info.get("user_id") or "").strip()
        if not user_id:
            return {"success": False, "error": f"消息 {normalized_msg_id} 缺少发送者 QQ 号", "msg_id": normalized_msg_id}

        user_nickname = str(user_info.get("user_nickname") or "").strip()
        user_cardname = str(user_info.get("user_cardname") or "").strip()
        display_name = user_cardname or user_nickname or user_id
        return {
            "success": True,
            "content": f"消息 {normalized_msg_id} 的发送者是 {display_name}，QQ 号为 {user_id}。",
            "msg_id": normalized_msg_id,
            "user_id": user_id,
            "qq": user_id,
            "user_nickname": user_nickname,
            "user_cardname": user_cardname,
            "display_name": display_name,
            "platform": str(query_result.get("platform") or "").strip(),
            "session_id": str(query_result.get("session_id") or target_stream_id).strip(),
        }

    @MessageGateway(
        name=NAPCAT_GATEWAY_NAME,
        route_type="duplex",
        platform="qq",
        protocol="napcat",
        description="NapCat WebSocket 双工消息网关（支持 client/server）",
    )
    async def handle_napcat_gateway(
        self,
        message: Dict[str, Any],
        route: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """处理 Host 出站消息并发送到 NapCat。

        Args:
            message: Host 侧标准 ``MessageDict``。
            route: Platform IO 生成的路由信息。
            metadata: Platform IO 附带的投递元数据。
            **kwargs: 预留扩展参数。

        Returns:
            Dict[str, Any]: 标准化后的发送结果。
        """
        del metadata
        del kwargs

        runtime_bundle = self._require_runtime_bundle()
        try:
            action_name, params = runtime_bundle.outbound_codec.build_outbound_action(message, route or {})
            response = await runtime_bundle.transport.call_action(action_name, params)
        except Exception as exc:
            return {"success": False, "error": str(exc)}

        if str(response.get("status", "")).lower() != "ok":
            return {
                "success": False,
                "error": str(response.get("wording") or response.get("message") or "NapCat send failed"),
                "metadata": {"retcode": response.get("retcode")},
            }

        response_data = response.get("data", {})
        internal_message_id = str(message.get("message_id") or "").strip()
        external_message_id = ""
        if isinstance(response_data, Mapping):
            external_message_id = str(response_data.get("message_id") or "")

        adapter_callbacks = []
        if internal_message_id and external_message_id and internal_message_id != external_message_id:
            adapter_callbacks.append(
                {
                    "name": "message_id_echo",
                    "payload": {
                        "content": {
                            "type": "echo",
                            "echo": internal_message_id,
                            "actual_id": external_message_id,
                        }
                    },
                }
            )

        return {
            "success": True,
            "external_message_id": external_message_id or None,
            "metadata": {
                "action": action_name,
                "adapter_callbacks": adapter_callbacks,
            },
        }

    def _ensure_runtime_components(self) -> None:
        """确保运行时依赖对象已经完成初始化。"""
        if self._event_router is None:
            self._event_router = NapCatEventRouter(
                gateway_capability=self.ctx.gateway,
                logger=self.ctx.logger,
                gateway_name=NAPCAT_GATEWAY_NAME,
                load_settings=self._load_settings,
            )

        if self._runtime_bundle is None:
            runtime_builder = NapCatRuntimeBuilder(
                gateway_capability=self.ctx.gateway,
                logger=self.ctx.logger,
                gateway_name=NAPCAT_GATEWAY_NAME,
            )
            self._runtime_bundle = runtime_builder.build(
                on_connection_opened=self._event_router.bootstrap_adapter_runtime_state,
                on_connection_closed=self._event_router.handle_transport_disconnected,
                on_payload=self._event_router.handle_transport_payload,
                on_natural_lift=self._event_router.emit_natural_lift_notice,
                on_heartbeat_timeout=self._event_router.handle_heartbeat_timeout,
            )
            self._event_router.bind_runtime(self._runtime_bundle)
            self._bind_runtime_aliases(self._runtime_bundle)

    async def _sync_private_chat_tool_component_state(self) -> None:
        """按配置同步主动私聊工具组件的启停状态。"""

        enabled = self._load_settings().plugin.enable_private_chat_tool
        tool_names = ("open_private_chat", "get_qq_by_msg_id")
        try:
            for tool_name in tool_names:
                if enabled:
                    result = await self.ctx.component.enable_component(tool_name, "TOOL")
                else:
                    result = await self.ctx.component.disable_component(tool_name, "TOOL")
                if isinstance(result, Mapping) and not bool(result.get("success", False)):
                    self.ctx.logger.warning(
                        f"NapCat 同步主动私聊工具启停状态失败: tool={tool_name} "
                        f"error={result.get('error') or result}"
                    )
        except Exception as exc:
            self.ctx.logger.warning(f"NapCat 同步主动私聊工具启停状态失败: {exc}")

    def _bind_runtime_aliases(self, runtime_bundle: NapCatRuntimeBundle) -> None:
        """同步运行时组件到插件级别的快捷引用。

        Args:
            runtime_bundle: 已初始化的运行时组件集合。
        """
        self._action_service = runtime_bundle.action_service
        self._query_service = runtime_bundle.query_service

    def _load_settings(self) -> NapCatPluginSettings:
        """返回当前生效的插件配置。

        Returns:
            NapCatPluginSettings: 当前生效的插件配置。
        """
        return cast(NapCatPluginSettings, self.config)

    async def _restart_connection_if_needed(self) -> None:
        """根据当前配置重启连接循环。"""
        self._ensure_runtime_components()
        runtime_bundle = self._require_runtime_bundle()
        settings = self._load_settings()

        await self._stop_connection()
        if not settings.should_connect():
            self.ctx.logger.info("NapCat 适配器保持空闲状态，因为插件或配置未启用")
            return
        if not settings.validate_runtime_config(self.ctx.logger):
            return
        if not runtime_bundle.transport.is_available():
            self.ctx.logger.error("NapCat 适配器依赖 aiohttp，但当前环境未安装该依赖")
            return

        if not settings.chat.enable_chat_list_filter:
            self.ctx.logger.info(
                "NapCat 聊天名单过滤已关闭：将忽略 group_list 与 private_list，仅保留 ban_user_id 和官方机器人屏蔽规则"
            )

        runtime_bundle.regex_filter.reload_patterns(settings.filters.regex_filter_patterns)
        if settings.filters.regex_filter_enabled and settings.filters.regex_filter_patterns:
            self.ctx.logger.info(
                f"NapCat 正则消息过滤已启用: 模式={settings.filters.regex_filter_mode}，"
                f"规则数={len(settings.filters.regex_filter_patterns)}"
            )
        if not settings.notice.enabled:
            self.ctx.logger.info("NapCat 通知事件转发已整体关闭：所有通知都不会传入 Host")

        runtime_bundle.transport.configure(settings.napcat_server)
        await runtime_bundle.transport.start()

    async def _stop_connection(self) -> None:
        """停止当前连接并清理运行时缓存。"""
        runtime_bundle = self._runtime_bundle
        if runtime_bundle is None:
            return

        await runtime_bundle.transport.stop()
        if self._event_router is not None:
            self._event_router.reset_caches()

    def _require_runtime_bundle(self) -> NapCatRuntimeBundle:
        """返回当前已初始化的运行时组件集合。

        Returns:
            NapCatRuntimeBundle: 当前运行时组件集合。

        Raises:
            RuntimeError: 当运行时尚未初始化时抛出。
        """
        self._ensure_runtime_components()
        runtime_bundle = self._runtime_bundle
        if runtime_bundle is None:
            raise RuntimeError("NapCat 运行时尚未初始化")
        return runtime_bundle


def create_plugin() -> NapCatAdapterPlugin:
    """创建插件实例。

    Returns:
        NapCatAdapterPlugin: NapCat 内置适配器插件实例。
    """
    return NapCatAdapterPlugin()
