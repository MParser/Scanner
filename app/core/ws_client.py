import json
import asyncio
import websockets
from uuid import uuid4
from dataclasses import dataclass, asdict, field
from websockets.exceptions import ConnectionClosed
from typing import Optional, Dict, Any, List, Literal
from app.core.logger import log


@dataclass
class WebSocketRequest:
    api: str
    params: Dict[str, Any] = field(default_factory=dict)
    request_id: str = field(default_factory=lambda: uuid4().hex)

    def __str__(self) -> str:
        return json.dumps(asdict(self))


@dataclass
class WebSocketResponse(Exception):
    type: Literal["response", "error"]
    code: int = 200
    message: str = "success"
    data: Optional[Dict[str, Any]] = None
    request_id: Optional[str] = None
    Bytes: Optional[bytes] = None

    def __post_init__(self):
        super().__init__(self.message)

    def __str__(self) -> str:
        result = {
            "type": self.type,
            "code": self.code,
            "message": self.message,
            "request_id": self.request_id
        }
        if self.data is not None:
            result["data"] = self.data
        return json.dumps(result, ensure_ascii=False)

    @property
    def success(self) -> bool:
        return self.type == "response"

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WebSocketResponse":
        valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered_data = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered_data)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class WebSocketClient:
    def __init__(self, base_url: str, client_id: Optional[str] = None):
        self.base_url = base_url.rstrip('/')
        self.client_id = client_id or uuid4().hex
        self.url = f"{self.base_url}/{self.client_id}"
        self.ws = None
        self._receive_task = None
        self._running = False
        self._pending_requests: Dict[str, asyncio.Future] = {}
        self._current_file_request = None
        self._file_chunks: List[bytes] = []

    async def is_connected(self) -> bool:
        if self.ws is None or self.ws.closed:
            return False
        
        try:
            await self.ws.send(str(WebSocketRequest(api="check_connection")))
            return True
        except Exception as e:
            log.error(f"检查连接失败: {str(e)}")
            return False

    async def connect(self) -> None:
        if await self.is_connected():
            return
            
        try:
            self.ws = await websockets.connect(self.url)
            self._running = True
            self._receive_task = asyncio.create_task(self._message_handler())
        except Exception as e:
            log.error(f"WebSocket连接失败: {str(e)}")
            raise WebSocketResponse(type="error", code=400, message=f"WebSocket连接失败: {str(e)}")

    async def _message_handler(self):
        while self._running and self.ws and not self.ws.closed:
            try:
                message = await self.ws.recv()
                if isinstance(message, bytes):
                    if self._current_file_request:
                        self._file_chunks.append(message)
                    continue

                data = json.loads(message)
                
                if data.get("type") == "check":
                    continue
                    
                if data.get("type") == "file":
                    await self._handle_file_message(data)
                    continue
                    
                response = WebSocketResponse.from_dict(data)
                if response.request_id in self._pending_requests:
                    await self._handle_response_message(response)
                    
            except ConnectionClosed:
                log.warning("WebSocket连接已关闭")
                await self._handle_connection_error()
                break
            except Exception as e:
                log.error(f"消息处理错误: {str(e)}")

    async def _handle_file_message(self, data: Dict[str, Any]):
        request_id = data.get("request_id")
        if not request_id:
            return
            
        if data.get("data") == "start":
            self._current_file_request = request_id
            self._file_chunks = []
        elif data.get("data") == "end":
            return

    async def _handle_response_message(self, response: WebSocketResponse):
        if not response.request_id:
            return
            
        future = self._pending_requests.pop(response.request_id)
        
        if not response.success:
            if response.request_id == self._current_file_request:
                self._current_file_request = None
                self._file_chunks = []
            future.set_exception(response)
            return
            
        if response.request_id == self._current_file_request:
            if not self._file_chunks:
                error_response = WebSocketResponse(type="error", code=403, message="文件传输异常: 没有收到文件数据", request_id=response.request_id)
                future.set_exception(error_response)
                return
                
            response.Bytes = b"".join(self._file_chunks)
            self._current_file_request = None
            self._file_chunks = []
                
        future.set_result(response)

    async def _handle_connection_error(self):
        self._running = False
        if self.ws:
            await self.ws.close()
            self.ws = None
            
        # 优先处理文件传输中断
        if self._current_file_request:
            future = self._pending_requests.pop(self._current_file_request, None)
            if future and not future.done():
                future.set_exception(WebSocketResponse(type="error", code=404, message="文件传输中断: WebSocket连接已断开", request_id=self._current_file_request))
            self._current_file_request = None
            self._file_chunks = []
            
        # 处理其他待处理的请求
        for request_id, future in self._pending_requests.items():
            if not future.done():
                future.set_exception(WebSocketResponse(type="error", code=404, message="WebSocket连接已断开", request_id=request_id))
        self._pending_requests.clear()
        
    async def close(self):
        self._running = False
        
        # 先取消消息处理任务
        if self._receive_task and not self._receive_task.done():
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
        
        # 处理所有待处理的请求
        if self._current_file_request:
            future = self._pending_requests.pop(self._current_file_request, None)
            if future and not future.done():
                future.set_exception(WebSocketResponse(type="error", code=404, message="文件传输中断: 连接已关闭", request_id=self._current_file_request))
            self._current_file_request = None
            self._file_chunks = []
            
        for request_id, future in self._pending_requests.items():
            if not future.done():
                future.set_exception(WebSocketResponse(type="error", code=404, message="连接已关闭", request_id=request_id))
        self._pending_requests.clear()
        
        # 最后关闭websocket连接
        if self.ws:
            await self.ws.close()
            self.ws = None

    async def send_request(self, api: str, params: Optional[Dict[str, Any]] = None, request_id: Optional[str] = None, timeout: float = 300.0) -> WebSocketResponse:
        request_id = request_id or uuid4().hex
        if not await self.is_connected():
            raise WebSocketResponse(type="error", code=400, message="WebSocket未连接", request_id=request_id)
            
        request = WebSocketRequest(api=api, params=params or {}, request_id=request_id)
        future = asyncio.get_running_loop().create_future()
        self._pending_requests[request.request_id] = future
        
        try:
            await self.ws.send(str(request))
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.CancelledError:
            self._pending_requests.pop(request.request_id, None)
            if request.request_id == self._current_file_request:
                self._current_file_request = None
                self._file_chunks = []
            raise WebSocketResponse(type="error", code=404, message="请求已取消", request_id=request.request_id)
        except asyncio.TimeoutError:
            self._pending_requests.pop(request.request_id, None)
            if request.request_id == self._current_file_request:
                self._current_file_request = None
                self._file_chunks = []
            raise WebSocketResponse(type="error", code=401, message=f"请求超时: {api}", request_id=request.request_id)
        except Exception as e:
            self._pending_requests.pop(request.request_id, None)
            if request.request_id == self._current_file_request:
                self._current_file_request = None
                self._file_chunks = []
            if isinstance(e, WebSocketResponse):
                raise
            raise WebSocketResponse(type="error", code=402, message=str(e), request_id=request.request_id)

    async def __aenter__(self):
        await self.connect()  # 失败时会抛出异常
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()