"""NapCat 正向 WebSocket 传输层。"""

from typing import TYPE_CHECKING, Any, Callable, Coroutine, Dict, Optional, Set, cast
from uuid import uuid4

import asyncio
import contextlib
import json

from .config import NapCatServerConfig

if TYPE_CHECKING:
    from aiohttp import ClientWebSocketResponse as AiohttpClientWebSocketResponse

try:
    from aiohttp import ClientSession, ClientTimeout, WSMsgType, web

    AIOHTTP_AVAILABLE = True
except ImportError:
    ClientSession = cast(Any, None)
    ClientTimeout = cast(Any, None)
    WSMsgType = cast(Any, None)
    web = cast(Any, None)
    AIOHTTP_AVAILABLE = False

if not TYPE_CHECKING:
    AiohttpClientWebSocketResponse = Any


class NapCatTransportClient:
    """NapCat WebSocket 传输客户端（支持 client/server 双模式）。"""

    def __init__(
        self,
        logger: Any,
        on_connection_opened: Callable[[], Coroutine[Any, Any, None]],
        on_connection_closed: Callable[[], Coroutine[Any, Any, None]],
        on_payload: Callable[[Dict[str, Any]], Coroutine[Any, Any, None]],
    ) -> None:
        """初始化传输层客户端。

        Args:
            logger: 插件日志对象。
            on_connection_opened: 连接建立后的异步回调。
            on_connection_closed: 连接断开后的异步回调。
            on_payload: 收到非 echo 载荷后的异步回调。
        """
        self._logger = logger
        self._on_connection_opened = on_connection_opened
        self._on_connection_closed = on_connection_closed
        self._on_payload = on_payload
        self._server_config: Optional[NapCatServerConfig] = None
        self._connection_task: Optional[asyncio.Task[None]] = None
        self._server_runner: Optional[Any] = None
        self._pending_actions: Dict[str, asyncio.Future[Dict[str, Any]]] = {}
        self._background_tasks: Set[asyncio.Task[Any]] = set()
        self._send_lock = asyncio.Lock()
        self._ws: Optional[AiohttpClientWebSocketResponse] = None
        self._stop_requested: bool = False
        self._connection_active: bool = False
        self._warned_missing_token_for_ws_url: Optional[str] = None

    @classmethod
    def is_available(cls) -> bool:
        """判断当前环境是否安装了传输层依赖。

        Returns:
            bool: 若已安装 ``aiohttp``，则返回 ``True``。
        """
        return AIOHTTP_AVAILABLE

    def configure(self, server_config: NapCatServerConfig) -> None:
        """更新当前传输层使用的 NapCat 服务端配置。

        Args:
            server_config: 最新生效的 NapCat 服务端配置。
        """
        self._server_config = server_config
        self._warned_missing_token_for_ws_url = None

    async def start(self) -> None:
        """启动 NapCat WebSocket 连接循环。

        Raises:
            RuntimeError: 当缺少配置或依赖时抛出。
        """
        if not self.is_available():
            raise RuntimeError("NapCat 适配器依赖 aiohttp，但当前环境未安装该依赖")
        if self._server_config is None:
            raise RuntimeError("NapCat 适配器尚未配置 napcat_server")
        if self._connection_task is not None and not self._connection_task.done():
            return

        self._stop_requested = False
        self._connection_task = asyncio.create_task(self._connection_loop(), name="napcat_adapter.connection")

    async def stop(self) -> None:
        """停止当前连接并清理所有后台任务。"""
        self._stop_requested = True
        connection_task = self._connection_task
        self._connection_task = None

        ws = self._ws
        if ws is not None and not ws.closed:
            with contextlib.suppress(Exception):
                await ws.close()
        self._ws = None

        if connection_task is not None:
            connection_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await connection_task

        server_runner = self._server_runner
        self._server_runner = None
        if server_runner is not None:
            with contextlib.suppress(Exception):
                await server_runner.cleanup()

        await self._cancel_background_tasks()
        await self._notify_connection_closed()
        self._fail_pending_actions("NapCat connection closed")

    async def call_action(self, action_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """发送 OneBot 动作并等待对应的 echo 响应。

        Args:
            action_name: OneBot 动作名称。
            params: 动作参数。

        Returns:
            Dict[str, Any]: NapCat 返回的原始响应字典。

        Raises:
            RuntimeError: 当连接不可用时抛出。
        """
        ws = self._ws
        server_config = self._server_config
        if ws is None or ws.closed or server_config is None:
            raise RuntimeError("NapCat is not connected")

        echo_id = uuid4().hex
        loop = asyncio.get_running_loop()
        response_future: asyncio.Future[Dict[str, Any]] = loop.create_future()
        self._pending_actions[echo_id] = response_future

        request_payload = {"action": action_name, "params": params, "echo": echo_id}
        try:
            async with self._send_lock:
                await ws.send_str(json.dumps(request_payload, ensure_ascii=False))
            return await asyncio.wait_for(response_future, timeout=server_config.action_timeout_sec)
        finally:
            self._pending_actions.pop(echo_id, None)

    async def _connection_loop(self) -> None:
        """根据配置选择 client/server 模式并维持连接。"""
        while not self._stop_requested:
            server_config = self._server_config
            if server_config is None:
                return

            try:
                if server_config.is_server_mode():
                    await self._server_loop(server_config)
                else:
                    await self._client_loop(server_config)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._logger.warning(
                    f"NapCat 适配器连接循环异常: {exc}"
                    f"{self._build_missing_token_hint(server_config)}"
                    f"{self._build_reconnect_hint(server_config)}"
                )

            if self._stop_requested:
                return

            await asyncio.sleep(server_config.reconnect_delay_sec)

    async def _client_loop(self, server_config: NapCatServerConfig) -> None:
        """维护 client 模式连接，并在断开后按配置重连。"""
        assert ClientSession is not None
        assert ClientTimeout is not None

        ws_url = server_config.build_ws_url()
        timeout = ClientTimeout(total=None, connect=10)
        self._log_connection_attempt(ws_url, server_config)

        try:
            async with ClientSession(headers=self._build_headers(server_config), timeout=timeout) as session:
                async with session.ws_connect(ws_url, heartbeat=server_config.heartbeat_interval or None) as ws:
                    self._ws = ws
                    self._logger.info(f"NapCat 适配器已连接: {ws_url}")
                    disconnect_reason = await self._receive_loop(ws)
                    self._log_connection_closed(ws_url, server_config, disconnect_reason)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._logger.warning(
                f"NapCat 适配器连接失败: {exc}"
                f"{self._build_missing_token_hint(server_config)}"
                f"{self._build_reconnect_hint(server_config)}"
            )
        finally:
            self._ws = None
            await self._notify_connection_closed()
            self._fail_pending_actions("NapCat connection interrupted")

    async def _server_loop(self, server_config: NapCatServerConfig) -> None:
        """维护 server 模式监听，等待 NapCat 主动连接。"""
        assert web is not None

        bind_host = server_config.host
        bind_port = server_config.port
        bind_label = f"ws://{bind_host}:{bind_port}{server_config.ws_path}"
        self._logger.info(f"NapCat 适配器已进入 server 模式，开始监听: {bind_label}")

        app = web.Application()
        app.router.add_get(server_config.ws_path, self._handle_ws_upgrade)

        runner = web.AppRunner(app)
        self._server_runner = runner
        await runner.setup()

        site = web.TCPSite(runner, host=bind_host, port=bind_port)
        await site.start()

        try:
            while not self._stop_requested:
                await asyncio.sleep(0.5)
        finally:
            if self._server_runner is runner:
                self._server_runner = None
            with contextlib.suppress(Exception):
                await runner.cleanup()

    async def _handle_ws_upgrade(self, request: Any) -> Any:
        """处理 server 模式下的 WebSocket 升级请求。"""
        assert web is not None

        server_config = self._server_config
        if server_config is None:
            return web.Response(status=503, text="NapCat adapter is not configured")

        if not self._check_server_authorization(request, server_config):
            return web.Response(status=401, text="Unauthorized")

        ws = web.WebSocketResponse(heartbeat=server_config.heartbeat_interval or None)
        await ws.prepare(request)

        previous_ws = self._ws
        if previous_ws is not None and not previous_ws.closed:
            with contextlib.suppress(Exception):
                await previous_ws.close(code=1000, message=b"Replaced by new NapCat connection")

        self._ws = ws
        peer = str(request.remote or "unknown")
        self._logger.info(f"NapCat 适配器收到连接: {peer}")

        try:
            disconnect_reason = await self._receive_loop(ws)
            self._logger.warning(f"NapCat 适配器连接已断开: {peer}，{disconnect_reason}{self._build_reconnect_hint(server_config)}")
        finally:
            if self._ws is ws:
                self._ws = None
                await self._notify_connection_closed()
                self._fail_pending_actions("NapCat connection interrupted")

        return ws

    def _check_server_authorization(self, request: Any, server_config: NapCatServerConfig) -> bool:
        """校验 server 模式下接入连接的鉴权信息。"""
        token = server_config.token
        if not token:
            return True

        auth_header = str(request.headers.get("Authorization") or "").strip()
        if auth_header.startswith("Bearer "):
            provided_token = auth_header[7:].strip()
            return provided_token == token

        return False

    async def _receive_loop(self, ws: AiohttpClientWebSocketResponse) -> str:
        """持续消费 WebSocket 消息并分发处理。

        Args:
            ws: 当前活跃的 WebSocket 连接对象。

        Returns:
            str: 当前连接结束时的简要原因描述。
        """
        assert WSMsgType is not None

        disconnect_reason = "未收到更多 WebSocket 消息，连接已结束"
        bootstrap_task = self._create_background_task(
            self._notify_connection_opened(),
            "napcat_adapter.bootstrap",
        )
        try:
            async for ws_message in ws:
                if ws_message.type != WSMsgType.TEXT:
                    if ws_message.type == WSMsgType.CLOSE:
                        disconnect_reason = self._describe_terminal_ws_message(
                            ws=ws,
                            ws_message=ws_message,
                            message_label="收到服务端 CLOSE 帧",
                        )
                        break
                    if ws_message.type == WSMsgType.CLOSED:
                        disconnect_reason = self._describe_terminal_ws_message(
                            ws=ws,
                            ws_message=ws_message,
                            message_label="WebSocket 已关闭",
                        )
                        break
                    if ws_message.type == WSMsgType.ERROR:
                        disconnect_reason = self._describe_terminal_ws_message(
                            ws=ws,
                            ws_message=ws_message,
                            message_label="WebSocket 进入错误状态",
                        )
                        break
                    continue

                payload = self._parse_json_message(ws_message.data)
                if payload is None:
                    continue

                if echo_id := str(payload.get("echo") or "").strip():
                    self._resolve_pending_action(echo_id, payload)
                    continue

                self._create_background_task(self._on_payload(payload), "napcat_adapter.payload")
        finally:
            if bootstrap_task is not None and not bootstrap_task.done():
                bootstrap_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await bootstrap_task

        return disconnect_reason

    def _create_background_task(self, coroutine: Coroutine[Any, Any, Any], name: str) -> asyncio.Task[Any]:
        """创建并跟踪一个后台任务。

        Args:
            coroutine: 待执行的协程对象。
            name: 任务名。

        Returns:
            asyncio.Task[Any]: 已创建的后台任务。
        """
        task = asyncio.create_task(coroutine, name=name)
        self._background_tasks.add(task)
        task.add_done_callback(self._handle_background_task_completion)
        return task

    def _handle_background_task_completion(self, task: asyncio.Task[Any]) -> None:
        """处理后台任务结束后的清理与异常记录。

        Args:
            task: 已结束的后台任务。
        """
        self._background_tasks.discard(task)
        if task.cancelled():
            return

        exception = task.exception()
        if exception is not None:
            self._logger.error(f"NapCat 适配器后台任务异常: {exception}", exc_info=True)

    async def _cancel_background_tasks(self) -> None:
        """取消所有仍在运行的后台任务。"""
        background_tasks = list(self._background_tasks)
        for task in background_tasks:
            task.cancel()
        if background_tasks:
            with contextlib.suppress(Exception):
                await asyncio.gather(*background_tasks, return_exceptions=True)
        self._background_tasks.clear()

    async def _notify_connection_opened(self) -> None:
        """在连接建立后触发上层回调。"""
        if self._connection_active:
            return

        self._connection_active = True
        try:
            await self._on_connection_opened()
        except Exception as exc:
            self._logger.warning(f"NapCat 适配器连接建立回调失败: {exc}")

    async def _notify_connection_closed(self) -> None:
        """在连接断开后触发上层回调。"""
        if not self._connection_active:
            return

        self._connection_active = False
        try:
            await self._on_connection_closed()
        except Exception as exc:
            self._logger.warning(f"NapCat 适配器断连回调失败: {exc}")

    def _resolve_pending_action(self, echo_id: str, payload: Dict[str, Any]) -> None:
        """解析等待中的动作响应。

        Args:
            echo_id: 动作请求对应的 echo 标识。
            payload: NapCat 返回的响应载荷。
        """
        response_future = self._pending_actions.get(echo_id)
        if response_future is None or response_future.done():
            return
        response_future.set_result(payload)

    def _fail_pending_actions(self, error_message: str) -> None:
        """让所有等待中的动作以异常方式结束。

        Args:
            error_message: 写入异常中的错误信息。
        """
        for response_future in self._pending_actions.values():
            if not response_future.done():
                response_future.set_exception(RuntimeError(error_message))
        self._pending_actions.clear()

    def _build_headers(self, server_config: NapCatServerConfig) -> Dict[str, str]:
        """构造连接 NapCat 所需的请求头。

        Args:
            server_config: 当前生效的 NapCat 服务端配置。

        Returns:
            Dict[str, str]: WebSocket 握手请求头。
        """
        return {"Authorization": f"Bearer {server_config.token}"} if server_config.token else {}

    def _log_connection_attempt(self, ws_url: str, server_config: NapCatServerConfig) -> None:
        """记录一次连接尝试的诊断信息。

        Args:
            ws_url: 即将连接的 WebSocket 地址。
            server_config: 当前生效的 NapCat 服务端配置。
        """
        auth_mode = "已配置 token" if server_config.token else "未配置 token"
        self._logger.debug(
            f"NapCat 适配器开始连接: {ws_url}（模式: {server_config.transport_mode}，鉴权: {auth_mode}）"
        )

        if not server_config.token and self._warned_missing_token_for_ws_url != ws_url:
            self._logger.warning(
                "NapCat 适配器当前未配置 napcat_server.token；"
                "若 NapCat 开启了访问令牌校验，连接可能会被服务端立即断开"
            )
            self._warned_missing_token_for_ws_url = ws_url

    def _log_connection_closed(self, ws_url: str, server_config: NapCatServerConfig, reason: str) -> None:
        """记录连接结束与重连计划。

        Args:
            ws_url: 当前连接对应的 WebSocket 地址。
            server_config: 当前生效的 NapCat 服务端配置。
            reason: 当前连接结束原因。
        """
        self._logger.warning(
            f"NapCat 适配器连接已断开: {ws_url}，{reason}"
            f"{self._build_missing_token_hint(server_config)}"
            f"{self._build_reconnect_hint(server_config)}"
        )

    def _build_missing_token_hint(self, server_config: NapCatServerConfig) -> str:
        """构造缺失 token 时的附加提示。

        Args:
            server_config: 当前生效的 NapCat 服务端配置。

        Returns:
            str: 缺失 token 时的提示文案；无需提示时返回空字符串。
        """
        if server_config.token:
            return ""
        return "；当前未配置 napcat_server.token，若服务端开启了访问令牌校验，请补全 token"

    def _build_reconnect_hint(self, server_config: NapCatServerConfig) -> str:
        """构造连接结束后的重连提示。

        Args:
            server_config: 当前生效的 NapCat 服务端配置。

        Returns:
            str: 自动重连提示；当停止请求已发出时返回空字符串。
        """
        if self._stop_requested:
            return ""
        return f"；将在 {server_config.reconnect_delay_sec:g} 秒后重连"

    def _describe_terminal_ws_message(
        self,
        ws: AiohttpClientWebSocketResponse,
        ws_message: Any,
        message_label: str,
    ) -> str:
        """描述导致连接结束的终止类 WebSocket 消息。

        Args:
            ws: 当前活跃的 WebSocket 连接对象。
            ws_message: aiohttp 返回的终止消息。
            message_label: 当前终止消息的人类可读标签。

        Returns:
            str: 汇总后的终止原因描述。
        """
        details = []
        close_code = getattr(ws, "close_code", None)
        if close_code not in (None, 0):
            details.append(f"close_code={close_code}")

        message_data = getattr(ws_message, "data", None)
        if message_data not in (None, "", 0, close_code):
            details.append(f"data={message_data}")

        message_extra = str(getattr(ws_message, "extra", "") or "").strip()
        if message_extra:
            details.append(f"extra={message_extra}")

        ws_exception = ws.exception()
        if ws_exception is not None:
            details.append(f"exception={ws_exception}")

        if not details:
            return message_label
        return f"{message_label}（{', '.join(str(item) for item in details)}）"

    def _parse_json_message(self, data: Any) -> Optional[Dict[str, Any]]:
        """解析 WebSocket 文本消息中的 JSON 数据。

        Args:
            data: WebSocket 收到的原始文本数据。

        Returns:
            Optional[Dict[str, Any]]: 成功时返回字典，失败时返回 ``None``。
        """
        try:
            payload = json.loads(str(data))
        except Exception as exc:
            self._logger.warning(f"NapCat 适配器解析 JSON 载荷失败: {exc}")
            return None

        return payload if isinstance(payload, dict) else None
