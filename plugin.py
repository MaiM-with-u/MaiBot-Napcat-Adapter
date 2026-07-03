"""内置 NapCat 适配器插件。

当前实现承担完整的 QQ / NapCat 消息网关职责：
1. 作为客户端连接 NapCat / OneBot v11 WebSocket 服务。
2. 将入站消息、通知事件与元事件转换为 Host 侧结构。
3. 将 Host 出站消息转换为 OneBot 动作并发送。
4. 通过公开 API 暴露 QQ 平台专属查询与管理动作。
"""

from __future__ import annotations

from typing import Any, ClassVar, Dict, Mapping, Optional, cast

from maibot_sdk import MaiBotPlugin, MessageGateway, PluginConfigBase

from .apis import (
    NapCatAccountApiMixin,
    NapCatFileApiMixin,
    NapCatGroupApiMixin,
    NapCatMessageApiMixin,
    NapCatSystemApiMixin,
)
from .config import NapCatPluginSettings
from .constants import NAPCAT_GATEWAY_NAME
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
        await self._restart_connection_if_needed()

    @MessageGateway(
        name=NAPCAT_GATEWAY_NAME,
        route_type="duplex",
        platform="qq",
        protocol="napcat",
        description="NapCat 正向 WebSocket 双工消息网关",
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
