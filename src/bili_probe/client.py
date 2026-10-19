"""B 站 Web 接口客户端，只依赖标准库。

几个容易踩的点，这里都处理了：

* 评论接口（``/x/v2/reply/main``）对无 cookie 的请求常常返回空列表，
  需要先取一组 buvid3 / buvid4 作为匿名身份；
* 接口统一返回 ``{"code": 0, "data": ...}``，code 非 0 时不能当成功；
* 网络抖动直接重试，412（风控）则给出明确提示，不盲目重试。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from .utils import ToolError

API = "https://api.bilibili.com"
REFERER = "https://www.bilibili.com/"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

_RETRY_CODES = {0}


class BiliAPIError(ToolError):
    """接口返回了非 0 的业务码。"""

    def __init__(self, code: int, message: str, path: str = "") -> None:
        suffix = f" ({path})" if path else ""
        super().__init__(f"接口错误 {code}: {message}{suffix}")
        self.code = code
        self.message = message


@dataclass
class Page:
    """视频的一个分P。"""

    cid: int
    index: int
    part: str
    duration: int


@dataclass
class VideoInfo:
    bvid: str
    aid: int
    title: str
    owner: str
    duration: int
    cover: str
    pages: list[Page] = field(default_factory=list)

    def page(self, index: int = 1) -> Page:
        for item in self.pages:
            if item.index == index:
                return item
        if not self.pages:
            raise ToolError(f"{self.bvid} 没有可用的分P信息")
        raise ToolError(f"{self.bvid} 只有 {len(self.pages)} 个分P，没有第 {index} 个")


class Client:
    """极简 HTTP 客户端，带重试与匿名 cookie。

    参数
    ----
    timeout: 单次请求超时（秒）
    retries: 网络错误重试次数
    sessdata: 登录凭据；只有需要 1080P+ 或大会员清晰度时才需要
    buvid: 是否自动获取匿名 buvid（评论接口需要）
    """

    def __init__(
        self,
        timeout: float = 20.0,
        retries: int = 3,
        sessdata: str | None = None,
        buvid: bool = True,
    ) -> None:
        self.timeout = timeout
        self.retries = max(0, retries)
        self._cookies: dict[str, str] = {}
        if sessdata:
            self._cookies["SESSDATA"] = sessdata
        if buvid:
            self._init_buvid()

    # ---------- 内部 ----------

    def _init_buvid(self) -> None:
        try:
            payload = self.get_json("/x/frontend/finger/spi", retry=False)
        except ToolError:
            return
        data = payload.get("data") or {}
        if data.get("b_3"):
            self._cookies.setdefault("buvid3", data["b_3"])
        if data.get("b_4"):
            self._cookies.setdefault("buvid4", data["b_4"])

    def _headers(self) -> dict[str, str]:
        headers = {"User-Agent": UA, "Referer": REFERER}
        if self._cookies:
            headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in self._cookies.items())
        return headers

    @property
    def headers(self) -> dict[str, str]:
        """当前请求头（含匿名 cookie），供下载器复用。"""
        return self._headers()

    def _request(self, url: str, timeout: float | None = None) -> bytes:
        request = urllib.request.Request(url, headers=self._headers())
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=timeout or self.timeout) as resp:
                    return resp.read()
            except urllib.error.HTTPError as exc:
                if exc.code == 412:
                    raise ToolError(
                        "被风控拦截（412）。降低请求频率，或通过 --sessdata 提供登录凭据。"
                    ) from exc
                if exc.code < 500:
                    raise ToolError(f"HTTP {exc.code}: {url}") from exc
                last_error = exc
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                last_error = exc
            if attempt < self.retries:
                time.sleep(1.5 ** attempt)
        raise ToolError(f"请求失败：{url}（{last_error}）")

    # ---------- 公开 ----------

    def get(self, path_or_url: str, params: dict | None = None, raw: bool = False) -> bytes:
        """GET 一个接口，返回原始字节或 JSON。"""
        url = path_or_url if path_or_url.startswith("http") else API + path_or_url
        if params:
            url = f"{url}{'&' if '?' in url else '?'}{urllib.parse.urlencode(params)}"
        payload = self._request(url)
        return payload if raw else json.loads(payload)

    def get_json(self, path: str, params: dict | None = None, retry: bool = True) -> dict:
        """GET 一个接口并校验业务码。"""
        if not retry:
            original, self.retries = self.retries, 0
            try:
                return self.get_json(path, params)
            finally:
                self.retries = original
        data = self.get(path, params)
        code = int(data.get("code", 0))
        if code != 0:
            raise BiliAPIError(code, str(data.get("message") or "未知错误"), path)
        return data

    def view(self, bvid: str) -> VideoInfo:
        """取视频元信息与分P列表。"""
        data = self.get_json("/x/web-interface/view", {"bvid": bvid}).get("data") or {}
        pages = [
            Page(
                cid=int(item.get("cid", 0)),
                index=int(item.get("page", idx + 1)),
                part=str(item.get("part") or ""),
                duration=int(item.get("duration", 0)),
            )
            for idx, item in enumerate(data.get("pages") or [])
        ]
        return VideoInfo(
            bvid=str(data.get("bvid") or bvid),
            aid=int(data.get("aid", 0)),
            title=str(data.get("title") or ""),
            owner=str((data.get("owner") or {}).get("name") or ""),
            duration=int(data.get("duration", 0)),
            cover=str(data.get("pic") or ""),
            pages=pages,
        )

    def playurl(self, bvid: str, cid: int, qn: int = 120) -> dict:
        """取播放地址。fnval=4048 表示要 DASH（音视频分离）。"""
        payload = self.get_json(
            "/x/player/playurl",
            {"bvid": bvid, "cid": cid, "qn": qn, "fnver": 0, "fnval": 4048, "fourk": 1},
        )
        return payload.get("data") or {}
