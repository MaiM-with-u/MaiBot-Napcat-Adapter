"""NapCat 内置适配器配置模型。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar, Dict, List, Literal, Optional, Tuple
from urllib.parse import urlparse

import logging

from maibot_sdk import Field, PluginConfigBase
from pydantic import ValidationInfo, field_validator, model_validator

from .constants import (
    DEFAULT_ACTION_TIMEOUT_SEC,
    DEFAULT_CHAT_LIST_TYPE,
    DEFAULT_HEARTBEAT_INTERVAL_SEC,
    DEFAULT_NAPCAT_HOST,
    DEFAULT_NAPCAT_PORT,
    DEFAULT_RECONNECT_DELAY_SEC,
    SUPPORTED_CONFIG_VERSION,
)

LOGGER = logging.getLogger("napcat_adapter.config")


def _schema_i18n(
    *,
    label_en: str,
    label_ja: str,
    hint_en: Optional[str] = None,
    hint_ja: Optional[str] = None,
    placeholder_en: Optional[str] = None,
    placeholder_ja: Optional[str] = None,
) -> Dict[str, Dict[str, str]]:
    """构造 WebUI 配置项多语言说明，保留外层中文字段兼容旧格式。"""

    i18n: Dict[str, Dict[str, str]] = {
        "en_US": {"label": label_en},
        "ja_JP": {"label": label_ja},
    }
    if hint_en is not None:
        i18n["en_US"]["hint"] = hint_en
    if hint_ja is not None:
        i18n["ja_JP"]["hint"] = hint_ja
    if placeholder_en is not None:
        i18n["en_US"]["placeholder"] = placeholder_en
    if placeholder_ja is not None:
        i18n["ja_JP"]["placeholder"] = placeholder_ja
    return i18n


def _notice_field(
    *,
    label: str,
    description: str,
    order: int,
    label_en: str,
    label_ja: str,
) -> Any:
    """构造通知事件开关字段，统一默认放行与 WebUI 多语言标签。"""

    return Field(
        default=True,
        description=description,
        json_schema_extra={
            "hint": description,
            "i18n": _schema_i18n(label_en=label_en, label_ja=label_ja),
            "label": label,
            "order": order,
        },
    )


class NapCatPluginOptions(PluginConfigBase):
    """插件级配置。"""

    __ui_label__: ClassVar[str] = "插件设置"
    __ui_order__: ClassVar[int] = 0

    enabled: bool = Field(
        default=False,
        description="是否启用 NapCat 适配器。",
        json_schema_extra={
            "hint": "关闭后插件会保持空闲，不会主动建立 NapCat WebSocket 连接。",
            "i18n": _schema_i18n(
                label_en="Enable adapter",
                label_ja="アダプターを有効化",
                hint_en="When disabled, the plugin stays idle and will not open a NapCat WebSocket connection.",
                hint_ja="無効にすると、プラグインは待機状態のままになり、NapCat WebSocket 接続を開始しません。",
            ),
            "label": "启用适配器",
            "order": 0,
        },
    )
    config_version: str = Field(
        default=SUPPORTED_CONFIG_VERSION,
        description="当前配置结构版本。",
        json_schema_extra={
            "disabled": True,
            "hidden": True,
            "i18n": _schema_i18n(label_en="Config version", label_ja="設定バージョン"),
            "label": "配置版本",
            "order": 99,
        },
    )

    def should_connect(self) -> bool:
        """判断当前配置下是否应当启动连接。

        Returns:
            bool: 若插件连接已启用，则返回 ``True``。
        """

        return self.enabled

    @field_validator("config_version", mode="before")
    @classmethod
    def _normalize_config_version(cls, value: Any) -> str:
        """规范化配置版本字段。

        Args:
            value: 原始配置值。

        Returns:
            str: 去除首尾空白后的配置版本；若为空则回退到当前支持版本。
        """

        normalized_value = _normalize_string(value)
        return normalized_value or SUPPORTED_CONFIG_VERSION


class NapCatServerConfig(PluginConfigBase):
    """NapCat 正向 WebSocket 连接配置。"""

    __ui_label__: ClassVar[str] = "NapCat 连接"
    __ui_order__: ClassVar[int] = 1

    host: str = Field(
        default=DEFAULT_NAPCAT_HOST,
        description="NapCat WebSocket 服务主机地址。",
        json_schema_extra={
            "hint": "通常为运行 NapCat 的宿主机地址，默认使用本机回环地址。",
            "i18n": _schema_i18n(
                label_en="Host address",
                label_ja="ホストアドレス",
                hint_en="Usually the host running NapCat. Defaults to the local loopback address.",
                hint_ja="通常は NapCat を実行しているホストです。既定ではローカルのループバックアドレスを使用します。",
                placeholder_en="127.0.0.1",
                placeholder_ja="127.0.0.1",
            ),
            "label": "主机地址",
            "order": 0,
            "placeholder": "127.0.0.1",
        },
    )
    port: int = Field(
        default=DEFAULT_NAPCAT_PORT,
        description="NapCat WebSocket 服务端口。",
        json_schema_extra={
            "hint": "与 NapCat 正向 WebSocket 服务监听端口保持一致。",
            "i18n": _schema_i18n(
                label_en="Port",
                label_ja="ポート",
                hint_en="Keep this consistent with the NapCat forward WebSocket listening port.",
                hint_ja="NapCat の正方向 WebSocket 待受ポートと一致させてください。",
            ),
            "label": "端口",
            "order": 1,
        },
    )
    token: str = Field(
        default="",
        description="NapCat 访问令牌，未启用鉴权时可留空。",
        json_schema_extra={
            "hint": "若 NapCat 开启了访问令牌校验，请在这里填写相同的 token。",
            "i18n": _schema_i18n(
                label_en="Access token",
                label_ja="アクセストークン",
                hint_en="If NapCat access token verification is enabled, enter the same token here.",
                hint_ja="NapCat でアクセストークン検証を有効にしている場合は、同じ token をここに入力してください。",
                placeholder_en="Optional",
                placeholder_ja="空欄可",
            ),
            "input_type": "password",
            "label": "访问令牌",
            "order": 2,
            "placeholder": "可留空",
        },
    )
    heartbeat_interval: float = Field(
        default=DEFAULT_HEARTBEAT_INTERVAL_SEC,
        description="心跳超时判定间隔，单位为秒。",
        json_schema_extra={
            "hint": "用于判断 NapCat 连接是否失活，必须大于 0。",
            "i18n": _schema_i18n(
                label_en="Heartbeat interval (sec)",
                label_ja="ハートビート間隔（秒）",
                hint_en="Used to detect whether the NapCat connection is stale. Must be greater than 0.",
                hint_ja="NapCat 接続が失活していないかを判定する間隔です。0 より大きい値にしてください。",
            ),
            "label": "心跳间隔（秒）",
            "order": 3,
            "step": 1,
        },
    )
    reconnect_delay_sec: float = Field(
        default=DEFAULT_RECONNECT_DELAY_SEC,
        description="连接断开后的重连等待时间，单位为秒。",
        json_schema_extra={
            "hint": "连接断开后会等待该时长再尝试重新连接。",
            "i18n": _schema_i18n(
                label_en="Reconnect delay (sec)",
                label_ja="再接続待機（秒）",
                hint_en="After a disconnect, wait this long before trying to reconnect.",
                hint_ja="接続が切断された後、再接続を試すまでこの時間待機します。",
            ),
            "label": "重连等待（秒）",
            "order": 4,
            "step": 1,
        },
    )
    action_timeout_sec: float = Field(
        default=DEFAULT_ACTION_TIMEOUT_SEC,
        description="调用 NapCat 动作接口的超时时间，单位为秒。",
        json_schema_extra={
            "hint": "发送消息、查询信息等动作会在超时后报错。",
            "i18n": _schema_i18n(
                label_en="Action timeout (sec)",
                label_ja="アクションタイムアウト（秒）",
                hint_en="Actions such as sending messages or querying info fail after this timeout.",
                hint_ja="メッセージ送信や情報取得などのアクションは、この時間を超えるとエラーになります。",
            ),
            "label": "动作超时（秒）",
            "order": 5,
            "step": 1,
        },
    )
    connection_id: str = Field(
        default="",
        description="可选连接标识，用于区分多条 NapCat 链路。",
        json_schema_extra={
            "hint": "当存在多条 NapCat 连接时，可用它作为路由作用域标识。",
            "i18n": _schema_i18n(
                label_en="Connection ID",
                label_ja="接続識別子",
                hint_en="When multiple NapCat connections exist, use this as the routing scope identifier.",
                hint_ja="複数の NapCat 接続がある場合、ルーティングスコープの識別子として使用できます。",
                placeholder_en="For example: primary",
                placeholder_ja="例：primary",
            ),
            "label": "连接标识",
            "order": 6,
            "placeholder": "例如：primary",
        },
    )

    def build_ws_url(self) -> str:
        """构造正向 WebSocket 地址。

        Returns:
            str: 供适配器作为客户端连接的 NapCat WebSocket 地址。
        """

        return f"ws://{self.host}:{self.port}"

    @field_validator("host", mode="before")
    @classmethod
    def _normalize_host(cls, value: Any) -> str:
        """规范化主机地址字段。

        Args:
            value: 原始配置值。

        Returns:
            str: 去除首尾空白后的主机地址；若为空则回退到默认主机。
        """

        normalized_value = _normalize_string(value)
        return normalized_value or DEFAULT_NAPCAT_HOST

    @field_validator("port", mode="before")
    @classmethod
    def _normalize_port(cls, value: Any) -> int:
        """规范化端口字段。

        Args:
            value: 原始配置值。

        Returns:
            int: 合法的正整数端口；非法时回退到默认端口。
        """

        return _normalize_positive_int(value, DEFAULT_NAPCAT_PORT)

    @field_validator("token", "connection_id", mode="before")
    @classmethod
    def _normalize_text_fields(cls, value: Any) -> str:
        """规范化文本字段。

        Args:
            value: 原始配置值。

        Returns:
            str: 去除首尾空白后的字符串值。
        """

        return _normalize_string(value)

    @field_validator(
        "heartbeat_interval",
        "reconnect_delay_sec",
        "action_timeout_sec",
        mode="before",
    )
    @classmethod
    def _normalize_positive_float_fields(cls, value: Any, info: ValidationInfo) -> float:
        """规范化正浮点数字段。

        Args:
            value: 原始配置值。
            info: Pydantic 字段校验上下文。

        Returns:
            float: 合法的正浮点数；非法时回退到对应默认值。
        """

        default_values: Dict[str, float] = {
            "action_timeout_sec": DEFAULT_ACTION_TIMEOUT_SEC,
            "heartbeat_interval": DEFAULT_HEARTBEAT_INTERVAL_SEC,
            "reconnect_delay_sec": DEFAULT_RECONNECT_DELAY_SEC,
        }
        return _normalize_positive_float(value, default_values[str(info.field_name)])


class NapCatChatConfig(PluginConfigBase):
    """聊天名单配置。"""

    __ui_label__: ClassVar[str] = "聊天过滤"
    __ui_order__: ClassVar[int] = 2

    enable_chat_list_filter: bool = Field(
        default=True,
        description="是否启用群聊与私聊名单过滤。",
        json_schema_extra={
            "hint": "关闭后将忽略群聊名单和私聊名单，仅保留全局屏蔽用户与官方机器人屏蔽规则。",
            "i18n": _schema_i18n(
                label_en="Enable chat list filter",
                label_ja="チャットリストフィルターを有効化",
                hint_en="When disabled, group and private lists are ignored; only global banned users and official bot blocking remain.",
                hint_ja="無効にすると、グループ/個人チャットのリストを無視し、全体のブロックユーザーと公式 Bot ブロックのみを適用します。",
            ),
            "label": "启用聊天名单过滤",
            "order": 0,
        },
    )
    show_dropped_chat_list_messages: bool = Field(
        default=False,
        description="是否显示未通过聊天名单过滤而被丢弃的消息日志。",
        json_schema_extra={
            "hint": "关闭后不会记录群聊/私聊因未通过聊天名单过滤而被丢弃的日志，默认关闭以减少刷屏。",
            "i18n": _schema_i18n(
                label_en="Show dropped chat-list logs",
                label_ja="チャットリストで破棄されたログを表示",
                hint_en="When disabled, dropped group/private chat-list logs are not recorded. Default off to reduce log noise.",
                hint_ja="無効にすると、チャットリストで破棄されたグループ/個人チャットのログを記録しません。ログの増加を抑えるため既定ではオフです。",
            ),
            "label": "显示聊天名单丢弃日志",
            "order": 1,
        },
    )
    group_list_type: Literal["whitelist", "blacklist"] = Field(
        default=DEFAULT_CHAT_LIST_TYPE,
        description="群聊名单模式。",
        json_schema_extra={
            "hint": "白名单模式只接收列表内群聊，黑名单模式则忽略列表内群聊。",
            "i18n": _schema_i18n(
                label_en="Group list mode",
                label_ja="グループリストモード",
                hint_en="Whitelist mode only accepts listed groups; blacklist mode ignores listed groups.",
                hint_ja="ホワイトリストではリスト内のグループのみ受信し、ブラックリストではリスト内のグループを無視します。",
            ),
            "label": "群聊名单模式",
            "order": 2,
        },
    )
    group_list: List[str] = Field(
        default_factory=list,
        description="群聊名单中的群号列表。",
        json_schema_extra={
            "hint": "群号会被统一转换为字符串并自动去重。",
            "i18n": _schema_i18n(
                label_en="Group list",
                label_ja="グループリスト",
                hint_en="Group IDs are normalized to strings and deduplicated automatically.",
                hint_ja="グループ ID は文字列に正規化され、自動的に重複排除されます。",
                placeholder_en="Enter group ID",
                placeholder_ja="グループ ID を入力",
            ),
            "label": "群聊名单",
            "order": 3,
            "placeholder": "请输入群号",
        },
    )
    private_list_type: Literal["whitelist", "blacklist"] = Field(
        default=DEFAULT_CHAT_LIST_TYPE,
        description="私聊名单模式。",
        json_schema_extra={
            "hint": "白名单模式只接收列表内私聊，黑名单模式则忽略列表内私聊。",
            "i18n": _schema_i18n(
                label_en="Private list mode",
                label_ja="個人チャットリストモード",
                hint_en="Whitelist mode only accepts listed private chats; blacklist mode ignores listed private chats.",
                hint_ja="ホワイトリストではリスト内の個人チャットのみ受信し、ブラックリストではリスト内の個人チャットを無視します。",
            ),
            "label": "私聊名单模式",
            "order": 4,
        },
    )
    private_list: List[str] = Field(
        default_factory=list,
        description="私聊名单中的用户 ID 列表。",
        json_schema_extra={
            "hint": "用户 ID 会被统一转换为字符串并自动去重。",
            "i18n": _schema_i18n(
                label_en="Private list",
                label_ja="個人チャットリスト",
                hint_en="User IDs are normalized to strings and deduplicated automatically.",
                hint_ja="ユーザー ID は文字列に正規化され、自動的に重複排除されます。",
                placeholder_en="Enter user ID",
                placeholder_ja="ユーザー ID を入力",
            ),
            "label": "私聊名单",
            "order": 5,
            "placeholder": "请输入用户 ID",
        },
    )
    ban_user_id: List[str] = Field(
        default_factory=list,
        description="全局屏蔽的用户 ID 列表。",
        json_schema_extra={
            "hint": "这些用户的消息会在进入 Host 之前被直接丢弃。",
            "i18n": _schema_i18n(
                label_en="Globally blocked users",
                label_ja="全体ブロックユーザー",
                hint_en="Messages from these users are dropped before entering the Host.",
                hint_ja="これらのユーザーからのメッセージは Host に入る前に破棄されます。",
                placeholder_en="Enter user ID",
                placeholder_ja="ユーザー ID を入力",
            ),
            "label": "全局屏蔽用户",
            "order": 6,
            "placeholder": "请输入用户 ID",
        },
    )
    ban_qq_bot: bool = Field(
        default=False,
        description="是否屏蔽 QQ 官方机器人消息。",
        json_schema_extra={
            "hint": "开启后会忽略来自 QQ 官方机器人或频道机器人的消息。",
            "i18n": _schema_i18n(
                label_en="Block official bots",
                label_ja="公式 Bot をブロック",
                hint_en="When enabled, messages from QQ official bots or channel bots are ignored.",
                hint_ja="有効にすると、QQ 公式 Bot またはチャンネル Bot からのメッセージを無視します。",
            ),
            "label": "屏蔽官方机器人",
            "order": 7,
        },
    )

    @field_validator("group_list_type", "private_list_type", mode="before")
    @classmethod
    def _normalize_list_types(cls, value: Any) -> Literal["whitelist", "blacklist"]:
        """规范化名单模式字段。

        Args:
            value: 原始配置值。

        Returns:
            Literal["whitelist", "blacklist"]: 合法的名单模式；非法时回退到默认值。
        """

        return _normalize_list_mode(value)

    @field_validator("group_list", "private_list", "ban_user_id", mode="before")
    @classmethod
    def _normalize_id_lists(cls, value: Any) -> List[str]:
        """规范化 ID 列表字段。

        Args:
            value: 原始配置值。

        Returns:
            List[str]: 规范化后的字符串列表，已去除空白与重复项。
        """

        return _normalize_string_list(value)


class NapCatFilterConfig(PluginConfigBase):
    """消息过滤配置。"""

    __ui_label__: ClassVar[str] = "消息过滤"
    __ui_order__: ClassVar[int] = 4

    ignore_self_message: bool = Field(
        default=True,
        description="是否忽略机器人自身发送的消息。",
        json_schema_extra={
            "hint": "建议保持开启，避免机器人处理自己刚刚发出的消息。",
            "i18n": _schema_i18n(
                label_en="Ignore self messages",
                label_ja="自身のメッセージを無視",
                hint_en="Recommended on to avoid the bot processing messages it just sent.",
                hint_ja="Bot が自分で送信した直後のメッセージを処理しないよう、有効のままにすることを推奨します。",
            ),
            "label": "忽略自身消息",
            "order": 0,
        },
    )
    regex_filter_enabled: bool = Field(
        default=False,
        description="是否启用正则表达式消息过滤。",
        json_schema_extra={
            "hint": "开启后将根据正则表达式规则过滤入站消息。",
            "label": "启用正则过滤",
            "order": 1,
        },
    )
    regex_filter_mode: Literal["blacklist", "whitelist"] = Field(
        default="blacklist",
        description="正则过滤模式。blacklist 匹配则丢弃，whitelist 仅放行匹配的消息。",
        json_schema_extra={
            "hint": "黑名单模式下匹配正则的消息会被丢弃；白名单模式下仅匹配正则的消息会被放行。",
            "label": "正则过滤模式",
            "order": 2,
        },
    )
    regex_filter_patterns: List[str] = Field(
        default_factory=list,
        description="正则表达式列表，支持 Python re 模块语法。",
        json_schema_extra={
            "hint": "每条规则为一个 Python 正则表达式，消息文本将逐条匹配。无效的正则表达式会在启动时记录警告并跳过。",
            "label": "正则表达式列表",
            "order": 3,
            "placeholder": r"例如：^广告.*|spam",
        },
    )
    regex_filter_show_dropped: bool = Field(
        default=False,
        description="是否显示未通过正则过滤而被丢弃的消息日志。",
        json_schema_extra={
            "hint": "关闭后不会记录因正则过滤而被丢弃的日志，默认关闭以减少刷屏。",
            "label": "显示正则过滤丢弃日志",
            "order": 4,
        },
    )

    @field_validator("regex_filter_mode", mode="before")
    @classmethod
    def _normalize_regex_filter_mode(cls, value: Any) -> Literal["whitelist", "blacklist"]:
        """规范化正则过滤模式字段。"""
        normalized_value = _normalize_string(value)
        if normalized_value == "whitelist":
            return "whitelist"
        if normalized_value not in ("whitelist", "blacklist"):
            LOGGER.warning(f"无效的 regex_filter_mode 值 '{value}'，已回退到 'blacklist'")
        return "blacklist"

    @field_validator("regex_filter_patterns", mode="before")
    @classmethod
    def _normalize_regex_filter_patterns(cls, value: Any) -> List[str]:
        """规范化正则表达式列表字段。"""
        return _normalize_string_list(value)


class NapCatNoticeConfig(PluginConfigBase):
    """通知事件传递配置。

    采用白名单式开关：仅明确启用的通知类型会注入 Host，
    未在此处列出的通知类型（如输入状态 notify.input_status）默认丢弃。
    """

    __ui_label__: ClassVar[str] = "通知事件"
    __ui_order__: ClassVar[int] = 3

    enabled: bool = Field(
        default=True,
        description="是否将 NapCat 推送的通知事件转发给 Host。",
        json_schema_extra={
            "hint": "关闭后，戳一戳、禁言、撤回、入群退群等所有通知事件都不会传入 Host。",
            "i18n": _schema_i18n(
                label_en="Enable notice events",
                label_ja="通知イベントを有効化",
                hint_en="When disabled, poke, ban, recall, member change and all other notice events are not routed to the Host.",
                hint_ja="無効にすると、poke、禁言、撤回、メンバー変更などすべての通知イベントは Host に転送されません。",
            ),
            "label": "启用通知事件",
            "order": 0,
        },
    )
    enable_poke: bool = _notice_field(
        label="传递戳一戳",
        description="是否传递戳一戳通知。",
        order=1,
        label_en="Route poke",
        label_ja="poke を転送",
    )
    enable_friend_recall: bool = _notice_field(
        label="传递好友撤回",
        description="是否传递好友消息撤回通知。",
        order=2,
        label_en="Route friend recall",
        label_ja="友達の撤回を転送",
    )
    enable_group_recall: bool = _notice_field(
        label="传递群消息撤回",
        description="是否传递群消息撤回通知。",
        order=3,
        label_en="Route group recall",
        label_ja="グループ撤回を転送",
    )
    enable_group_ban: bool = _notice_field(
        label="传递禁言",
        description="是否传递群禁言和解除禁言通知。",
        order=4,
        label_en="Route group ban",
        label_ja="グループ禁言を転送",
    )
    enable_group_msg_emoji_like: bool = _notice_field(
        label="传递消息表情回应",
        description="是否传递群消息表情回应通知。",
        order=5,
        label_en="Route emoji reactions",
        label_ja="絵文字リアクションを転送",
    )
    enable_group_upload: bool = _notice_field(
        label="传递群文件上传",
        description="是否传递群文件上传通知。",
        order=6,
        label_en="Route group uploads",
        label_ja="グループファイルを転送",
    )
    enable_group_increase: bool = _notice_field(
        label="传递入群",
        description="是否传递群成员增加通知。",
        order=7,
        label_en="Route member joins",
        label_ja="参加通知を転送",
    )
    enable_group_decrease: bool = _notice_field(
        label="传递退群",
        description="是否传递群成员减少通知。",
        order=8,
        label_en="Route member leaves",
        label_ja="退出通知を転送",
    )
    enable_group_admin: bool = _notice_field(
        label="传递管理员变动",
        description="是否传递群管理员变动通知。",
        order=9,
        label_en="Route admin changes",
        label_ja="管理者変更を転送",
    )
    enable_essence: bool = _notice_field(
        label="传递精华消息",
        description="是否传递精华消息变动通知。",
        order=10,
        label_en="Route essence changes",
        label_ja="精華メッセージを転送",
    )
    enable_group_name: bool = _notice_field(
        label="传递群名变更",
        description="是否传递群名称变更通知。",
        order=11,
        label_en="Route group name changes",
        label_ja="グループ名変更を転送",
    )


class NapCatPluginSettings(PluginConfigBase):
    """NapCat 插件完整配置。"""

    plugin: NapCatPluginOptions = Field(default_factory=NapCatPluginOptions)
    napcat_server: NapCatServerConfig = Field(default_factory=NapCatServerConfig)
    chat: NapCatChatConfig = Field(default_factory=NapCatChatConfig)
    notice: NapCatNoticeConfig = Field(default_factory=NapCatNoticeConfig)
    filters: NapCatFilterConfig = Field(default_factory=NapCatFilterConfig)

    @model_validator(mode="before")
    @classmethod
    def _upgrade_legacy_config(cls, raw_config: Any) -> Dict[str, Any]:
        """将旧版配置结构迁移为当前配置模型。

        Args:
            raw_config: Runner 注入的原始配置内容。

        Returns:
            Dict[str, Any]: 适配到当前配置模型后的字典结构。
        """

        raw_mapping = _as_mapping(raw_config)
        plugin_section = _as_mapping(raw_mapping.get("plugin"))
        server_section = _as_mapping(raw_mapping.get("napcat_server"))
        legacy_connection_section = _as_mapping(raw_mapping.get("connection"))
        chat_section = _as_mapping(raw_mapping.get("chat"))
        filters_section = _as_mapping(raw_mapping.get("filters"))
        notice_section = _as_mapping(raw_mapping.get("notice"))

        if legacy_connection_section:
            LOGGER.warning("NapCat 适配器检测到旧版 [connection] 配置段，已自动迁移到 [napcat_server]")

        if not server_section and legacy_connection_section:
            server_section = dict(legacy_connection_section)

        normalized_server_section = dict(server_section)
        legacy_host, legacy_port = _read_legacy_host_port(normalized_server_section, legacy_connection_section)
        current_host = _normalize_string(normalized_server_section.get("host"))
        if legacy_host and current_host in {"", DEFAULT_NAPCAT_HOST}:
            normalized_server_section["host"] = legacy_host

        current_port = _normalize_positive_int(normalized_server_section.get("port"), DEFAULT_NAPCAT_PORT)
        if legacy_port is not None and current_port == DEFAULT_NAPCAT_PORT:
            normalized_server_section["port"] = legacy_port

        legacy_access_token = _normalize_string(normalized_server_section.get("access_token")) or _normalize_string(
            legacy_connection_section.get("access_token")
        )
        if legacy_access_token and not _normalize_string(normalized_server_section.get("token")):
            LOGGER.warning("NapCat 适配器检测到旧版 access_token 配置，已自动迁移到 napcat_server.token")
            normalized_server_section["token"] = legacy_access_token

        legacy_heartbeat_value = normalized_server_section.get("heartbeat_sec", legacy_connection_section.get("heartbeat_sec"))
        current_heartbeat = _normalize_positive_float(
            normalized_server_section.get("heartbeat_interval"),
            DEFAULT_HEARTBEAT_INTERVAL_SEC,
        )
        legacy_heartbeat = _normalize_positive_float(legacy_heartbeat_value, DEFAULT_HEARTBEAT_INTERVAL_SEC)
        if legacy_heartbeat_value is not None and current_heartbeat == DEFAULT_HEARTBEAT_INTERVAL_SEC:
            LOGGER.warning(
                "NapCat 适配器检测到旧版 heartbeat_sec 配置，已自动迁移到 napcat_server.heartbeat_interval"
            )
            normalized_server_section["heartbeat_interval"] = legacy_heartbeat

        return {
            "chat": chat_section,
            "filters": filters_section,
            "notice": notice_section,
            "napcat_server": normalized_server_section,
            "plugin": plugin_section,
        }

    @classmethod
    def from_mapping(cls, raw_config: Mapping[str, Any], logger: Any) -> "NapCatPluginSettings":
        """从 Runner 注入的原始配置字典解析插件配置。

        Args:
            raw_config: Runner 注入的原始配置内容。
            logger: 兼容旧调用签名保留的日志对象，当前不直接使用。

        Returns:
            NapCatPluginSettings: 规范化后的插件配置模型。
        """

        del logger
        return cls.model_validate(dict(raw_config))

    def should_connect(self) -> bool:
        """判断当前配置下是否应当启动连接。

        Returns:
            bool: 若插件连接已启用，则返回 ``True``。
        """

        return self.plugin.should_connect()

    def validate_runtime_config(self, logger: Any) -> bool:
        """校验当前配置是否满足启动连接的前提条件。

        Args:
            logger: 插件日志对象。

        Returns:
            bool: 若配置满足启动连接的前提条件，则返回 ``True``。
        """

        config_version = self.plugin.config_version
        if not config_version:
            logger.error(f"NapCat 适配器配置缺少 plugin.config_version，当前插件要求版本 {SUPPORTED_CONFIG_VERSION}")
            return False

        if config_version != SUPPORTED_CONFIG_VERSION:
            logger.error(
                f"NapCat 适配器配置版本不兼容: 当前为 {config_version}，当前插件要求 {SUPPORTED_CONFIG_VERSION}"
            )
            return False

        if not self.napcat_server.host:
            logger.warning("NapCat 适配器已启用，但 napcat_server.host 为空")
            return False

        if self.napcat_server.port <= 0:
            logger.warning("NapCat 适配器已启用，但 napcat_server.port 不是正整数")
            return False

        return True


def _as_mapping(value: Any) -> Dict[str, Any]:
    """将任意值安全转换为字典。

    Args:
        value: 待转换的值。

    Returns:
        Dict[str, Any]: 若原值是映射，则返回普通字典；否则返回空字典。
    """

    return dict(value) if isinstance(value, Mapping) else {}


def _normalize_list_mode(value: Any) -> Literal["whitelist", "blacklist"]:
    """规范化名单模式字符串。

    Args:
        value: 原始配置值。

    Returns:
        Literal["whitelist", "blacklist"]: 合法的名单模式；非法时回退到默认值。
    """

    normalized_value = _normalize_string(value)
    if normalized_value == "whitelist":
        return "whitelist"
    if normalized_value == "blacklist":
        return "blacklist"
    return DEFAULT_CHAT_LIST_TYPE


def _normalize_positive_float(value: Any, default: float) -> float:
    """规范化正浮点数配置值。

    Args:
        value: 原始配置值。
        default: 非法取值时使用的默认值。

    Returns:
        float: 合法的正浮点数；非法时回退到默认值。
    """

    if isinstance(value, (int, float)) and float(value) > 0:
        return float(value)

    if isinstance(value, str):
        try:
            parsed_value = float(value.strip())
        except ValueError:
            return default
        if parsed_value > 0:
            return parsed_value

    return default


def _normalize_positive_int(value: Any, default: int) -> int:
    """规范化正整数配置值。

    Args:
        value: 原始配置值。
        default: 非法取值时使用的默认值。

    Returns:
        int: 合法的正整数；非法时回退到默认值。
    """

    if isinstance(value, int) and value > 0:
        return value

    if isinstance(value, str):
        normalized_value = value.strip()
        if normalized_value.isdigit():
            parsed_value = int(normalized_value)
            if parsed_value > 0:
                return parsed_value

    return default


def _normalize_string(value: Any) -> str:
    """规范化字符串配置值。

    Args:
        value: 原始配置值。

    Returns:
        str: 去除首尾空白后的字符串；若值为空则返回空字符串。
    """

    return "" if value is None else str(value).strip()


def _normalize_string_list(value: Any) -> List[str]:
    """规范化字符串列表配置值。

    Args:
        value: 原始配置值。

    Returns:
        List[str]: 去除空白与重复项后的字符串列表。
    """

    if not isinstance(value, list):
        return []

    normalized_values: List[str] = []
    seen_values = set()
    for item in value:
        item_text = _normalize_string(item)
        if not item_text or item_text in seen_values:
            continue
        seen_values.add(item_text)
        normalized_values.append(item_text)
    return normalized_values


def _read_legacy_host_port(
    server_section: Mapping[str, Any],
    legacy_connection_section: Mapping[str, Any],
) -> Tuple[str, Optional[int]]:
    """从旧版 ``ws_url`` 配置中提取主机与端口。

    Args:
        server_section: 新版 ``napcat_server`` 配置段。
        legacy_connection_section: 旧版 ``connection`` 配置段。

    Returns:
        Tuple[str, Optional[int]]: 解析到的主机与端口；若未找到，则返回空主机与 ``None``。
    """

    legacy_ws_url = _normalize_string(server_section.get("ws_url")) or _normalize_string(
        legacy_connection_section.get("ws_url")
    )
    if not legacy_ws_url:
        return "", None

    parsed_url = urlparse(legacy_ws_url)
    parsed_host = parsed_url.hostname or ""
    parsed_port = parsed_url.port

    LOGGER.warning("NapCat 适配器检测到旧版 ws_url 配置，已自动迁移到 napcat_server.host/port")
    if parsed_url.path not in {"", "/"}:
        LOGGER.warning("NapCat 适配器旧版 ws_url 包含路径，新的 napcat_server 配置不会保留该路径")

    return parsed_host, parsed_port
