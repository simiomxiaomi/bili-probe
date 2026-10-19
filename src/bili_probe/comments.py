"""评论区抓取（按热度分页）。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .client import Client
from .utils import ToolError

REPLY_URL = "/x/v2/reply/main"


@dataclass
class Comment:
    user: str
    message: str
    like: int
    ctime: int
    rpid: int
    replies: list["Comment"] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "user": self.user,
            "message": self.message,
            "like": self.like,
            "ctime": self.ctime,
            "rpid": self.rpid,
            "replies": [item.as_dict() for item in self.replies],
        }


def _parse(node: dict) -> Comment:
    content = node.get("content") or {}
    return Comment(
        user=str((node.get("member") or {}).get("uname") or ""),
        message=str(content.get("message") or "").replace("\n", " "),
        like=int(node.get("like", 0) or 0),
        ctime=int(node.get("ctime", 0) or 0),
        rpid=int(node.get("rpid", 0) or 0),
        replies=[_parse(item) for item in (node.get("replies") or [])],
    )


def fetch(
    client: Client,
    aid: int,
    pages: int = 1,
    sort: int = 2,
    with_replies: bool = False,
    delay: float = 0.6,
) -> list[Comment]:
    """按 ``sort=2``（按热度）拉取评论，最多 ``pages`` 页。

    ``with_replies`` 打开时会连楼中楼一起带回来（接口本身就会返回前几条）。
    """
    results: list[Comment] = []
    cursor = 0
    for page in range(max(1, pages)):
        payload = client.get_json(
            REPLY_URL, {"type": 1, "oid": aid, "next": cursor, "mode": 3, "sort": sort}
        )
        data = payload.get("data") or {}
        nodes = data.get("replies") or []
        if not nodes:
            break
        for node in nodes:
            comment = _parse(node)
            if not with_replies:
                comment.replies = []
            results.append(comment)

        cursor = int(((data.get("cursor") or {}).get("next")) or 0)
        if not cursor:
            break
        if page < pages - 1:
            time.sleep(delay)
    return results


def format_comments(items: list[Comment], limit: int = 15) -> str:
    lines = [f"评论数：{len(items)}", ""]
    for item in items[:limit]:
        lines.append(f"[{item.like:>5}] {item.user}：{item.message[:80]}")
    return "\n".join(lines)


def require_aid(aid: int) -> int:
    if not aid:
        raise ToolError("缺少 aid，无法请求评论接口")
    return aid
