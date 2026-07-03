"""NapCat 运行时组件容器。"""

from __future__ import annotations

from dataclasses import dataclass

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


@dataclass
class NapCatRuntimeBundle:
    """NapCat 运行时依赖集合。"""

    action_service: NapCatActionService
    ban_state_store: NapCatBanStateStore
    ban_tracker: NapCatBanTracker
    chat_filter: NapCatChatFilter
    heartbeat_monitor: NapCatHeartbeatMonitor
    inbound_codec: NapCatInboundCodec
    notice_codec: NapCatNoticeCodec
    notice_filter: NapCatNoticeFilter
    official_bot_guard: NapCatOfficialBotGuard
    outbound_codec: NapCatOutboundCodec
    query_service: NapCatQueryService
    runtime_state: NapCatRuntimeStateManager
    regex_filter: NapCatRegexFilter
    transport: NapCatTransportClient
