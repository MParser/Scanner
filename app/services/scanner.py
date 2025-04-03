import json
from typing import Dict, Optional
import re
import asyncio
from datetime import datetime
from app.core.logger import log
from app.utils.server import Server, Gateway
from app.core.errors import NotFoundError


server = Server()


class ResponseModel:
    def __init__(self, code: int = 200, data: dict = {}, message: str = ""):
        self.code = code
        self.data = data
        self.message = message
    
    def __str__(self):
        return json.dumps({
            'code': self.code,
            'data': self.data,
            'message': self.message
        }, ensure_ascii=False)


class Scanner:
    """NDS文件扫描器
    
    负责扫描NDS服务器上的文件，解析文件信息并提交到后端数据库。
    支持NDS实例并发扫描，每个实例独立运行。
    """

    def __init__(self):
        self.gateway: Dict[str, Gateway] = {}
        self._tasks: Dict[str, asyncio.Task] = {}  # 修复类型注解
        self.max_interval = 300 # 扫描间隔，单位秒
        self.min_interval = 60 # 最小扫描间隔，单位秒
        
        self.running = False
        
        self._time_pattern = re.compile(r'[_-](\d{14})')
        
        
        
    
    def _extract_time(self, filename: str) -> Optional[str]:
        try:
            match = self._time_pattern.search(filename)
            if match:
                time_str = match.group(1)
                # 解析时间字符串
                parsed_time = datetime.strptime(time_str, '%Y%m%d%H%M%S')
                # 转换为数据库格式
                return parsed_time.strftime('%Y-%m-%d %H:%M:%S')
        except Exception as e:
            log.warning(f"解析时间字符串失败: {str(e)}")
        return None
    
    async def scan_loop(self, nds_config):
        try:
            server = Server()
            gateway = Gateway(self.gateway, f"Scanner-NDS-{nds_config.get('id')}")
            while self.running:
                try:
                    await gateway.connect()
                    mro_files_nds = await gateway.scan_nds(nds_config.get("id"), nds_config.get("MRO_Path"), nds_config.get("MRO_Filter"))
                    mdt_files_nds = await gateway.scan_nds(nds_config.get("id"), nds_config.get("MDT_Path"), nds_config.get("MDT_Filter"))

                    if isinstance(mro_files_nds, ResponseModel) and mro_files_nds.code == 200:
                        mro_new_files = await server.ndsfile_filter_files(nds_config.get("id"), "MRO", str(mro_files_nds.data))
                        files = mro_new_files.get("missing", []) if mro_new_files else []
                        for mro_file in files:
                            print(mro_file)
                            mro_info = await gateway.zip_info(nds_config.get("id"), mro_file)
                            print(mro_info)
                            # 上传文件清单 ndsid, date_type, info
                    if isinstance(mdt_files_nds, ResponseModel) and mdt_files_nds.code == 200:
                        mdt_new_files = await server.ndsfile_filter_files(nds_config.get("id"), "MDT", str(mdt_files_nds.data))

                    await asyncio.sleep(self.max_interval)
                    
                    
                    
                except Exception as e:
                    log.error(f"扫描失败:{str(e)}")
                await asyncio.sleep(self.min_interval)
            if gateway.is_connected():
                await gateway.disconnect()
        except Exception as e:
            log.error(f"扫描器运行失败: {str(e)}")
    
    async def start(self):
        if self.running:
            return "扫描器已启动"
        self.running = True
        
        response = await server.info()
        if not response.get("gateway"):
            self.running = False
            raise ValueError("未配置网关")
        # 确保 gateway 是字典类型，避免类型不匹配
        gateway_data = response.get("gateway", {})
        if isinstance(gateway_data, dict):
            self.gateway = gateway_data
        else:
            self.gateway = {}
        if not response.get("ndsLinks") or response.get("ndsLinks") == []:
            self.running = False
            raise ValueError("未配置NDS")
        gateway = response.get("gateway")
        if gateway is None:
            raise ValueError("网关配置不存在")
        gateway_nds = await server.gateway_nds(gateway.get("id"))
        if not gateway_nds or gateway_nds == []:
            self.running = False
            raise ValueError("绑定网关DNS清单为空, 无法启动扫描器")
        
        
        ndsList =  response.get("ndsLinks")
        try:
            # 确保 ndsList 不为 None 且可迭代
            for config in (ndsList or []):
                if not config.get("id", None):
                    continue
                self._tasks[str(config.get("id"))] = asyncio.create_task(self.scan_loop(config.get("nds")))
            if self._tasks == {}:
                return ValueError("无可用NDS")
            return "扫描器启动成功"
        except Exception as e:
            log.error(f"扫描器启动失败: {str(e)}")
            self.running = False
            raise ValueError("扫描器启动失败")