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
    def __init__(self, code: int = 200, data: dict = None, message: str = ""):
        self.code = code
        self.data = data
        self.message = message
    
    def __str__(self):
        return json.dumps(self.dict(), ensure_ascii=False)


class Scanner:
    """NDS文件扫描器
    
    负责扫描NDS服务器上的文件，解析文件信息并提交到后端数据库。
    支持NDS实例并发扫描，每个实例独立运行。
    """

    def __init__(self):
        self.gateway: Dict = None
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
                    start_time = datetime.now()
                    mro_new_files = []
                    await gateway.connect()
                    mro_files_nds = await gateway.scan_nds(nds_config.get("id"), nds_config.get("MRO_Path"), nds_config.get("MRO_Filter"))
                    if mro_files_nds.code == 200:
                        mro_new_files = await server.ndsfile_filter_files(nds_config.get("id"), "MRO", mro_files_nds.data)
                    mdt_files_nds = await gateway.scan_nds(nds_config.get("id"), nds_config.get("MDT_Path"), nds_config.get("MDT_Filter"))
                    if mdt_files_nds.code == 200:
                        mdt_new_files = await server.ndsfile_filter_files(nds_config.get("id"), "MDT", mdt_files_nds.data)
                    
                    # 合并新文件并保留类型信息
                    new_files = [
                        *[{'path': path, 'type': 'MRO'} for path in mro_new_files],
                        *[{'path': path, 'type': 'MDT'} for path in mdt_new_files]
                    ]

                    # 扫描新文件子包
                    nds_id = int(nds_config.get("id"))  # 提前获取ID
                    batch_data = []  # 存储待处理的数据
                    batch_size = 0  # 当前批次的数据大小
                    MAX_BATCH_SIZE = 10 * 1024 * 1024  # 10MB
                    
                    for file in new_files:
                        data = await gateway.zip_info(nds=nds_config.get("id"), path=file['path'])
                        if data.code == 200:
                            # 批量添加ndsId和data_type
                            current_data = [{**item, 'ndsId': nds_id, 'data_type': file['type']} for item in data.data]
                            current_size = len(json.dumps(current_data).encode('utf-8'))
                            
                            # 如果当前批次加上新数据超过5MB，先处理当前批次
                            if batch_size + current_size > MAX_BATCH_SIZE and batch_data:
                                try:
                                    response = await server.batch_add_tasks(batch_data)
                                    if response.get('code') == 429:  # redis高负荷，暂停写入
                                        batch_data = []  # 清空未成功提交的数据
                                        batch_size = 0
                                        break
                                    elif response.get('code') == 200:
                                        log.info(f"批量添加文件成功: {response.get('data')}")
                                        # 成功提交后再重置批次数据
                                        batch_data = []
                                        batch_size = 0
                                    else:
                                        log.error(f"批量添加文件失败: {response.get('message')}")
                                        batch_data = []  # 提交失败也清空数据
                                        batch_size = 0
                                except Exception as e:
                                    log.error(f"批量添加文件失败: {str(e)}")
                                    batch_data = []
                                    batch_size = 0
                            
                            # 添加新数据到批次
                            batch_data.extend(current_data)
                            batch_size += current_size
                    
                    # 处理最后一批数据（如果有）
                    if batch_data:
                        try:
                            response = await server.batch_add_tasks(batch_data)
                            if response.get('code') == 200:
                                log.info(f"批量添加文件成功: {response.get('data')}")
                            else:
                                log.error(f"批量添加文件失败: {response.get('message')}")
                        except Exception as e:
                            log.error(f"批量添加文件失败: {str(e)}")
                            
                except Exception as e:
                    log.error(f"扫描失败:{str(e)}")
                
                end_time = datetime.now()
                elapsed_time = (end_time - start_time).total_seconds()
                interval = max(self.min_interval, self.max_interval - elapsed_time)
                await asyncio.sleep(interval)
                

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
        self.gateway = response.get("gateway")
        if not response.get("ndsLinks") or response.get("ndsLinks") == []:
            self.running = False
            raise ValueError("未配置NDS")
        gateway_nds = await server.gateway_nds(response.get("gateway").get("id"))
        if not gateway_nds or gateway_nds == []:
            self.running = False
            raise ValueError("绑定网关DNS清单为空, 无法启动扫描器")
        
        
        ndsList =  response.get("ndsLinks")
        try:
            for config in ndsList:
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