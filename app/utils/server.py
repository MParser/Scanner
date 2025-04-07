import json
from app.core.config import config
from app.core.http_client import HttpClient, HttpConfig
from app.core.ws_client import WebSocketClient, WebSocketResponse
from uuid import uuid4

class Server:
    def __init__(self):
        self.server = HttpClient(

            f"{config.get('server.protocol')}://{config.get('server.host')}:{config.get('server.port')}/api/",

            config=HttpConfig(timeout=config.get("server.timeout", 3600))
        )

        self.id = config.get("app.id")
    

    async def register(self):

        response = await self.server.post("scanner/register", json={

            "id": config.get("app.id"),

            "port": config.get("app.port"),

        })

        if response.get("code") == 200:

            return response.get("data")
        else:

            raise Exception(f"注册失败: {json.dumps(response, ensure_ascii=False)}")
        

    async def unregister(self):

        response = await self.server.put(f"scanner/{config.get('app.id')}", json={ "status": 0 })

        if response.get("code") == 200:

            return response.get("data")
        else:

            raise Exception(f"注销失败: {json.dumps(response, ensure_ascii=False)}")
        

    async def info(self):

        response = await self.server.get(f"scanner/{config.get('app.id')}")

        if response.get("code") == 200:

            return response.get("data")
        else:

            raise Exception(f"获取信息失败: {json.dumps(response, ensure_ascii=False)}")
    

    async def gateway_nds(self, gateway_id: str=None):

        if not gateway_id:

            raise Exception("Gateway_id 不能为空")

        response = await self.server.get(f"gateway/{gateway_id}")

        if response.get("code") == 200:

            return response.get("data").get("ndsLinks")
        else:

            raise Exception(f"获取网关DNS清单失败: {json.dumps(response, ensure_ascii=False)}")
        

    async def ndsfile_filter_files(self, ndsId: str, date_type: str, file_paths: str):

        '''

        过滤文件清单，获取任务规则内的文件清单以便后续扫描子包

        '''

        if not ndsId:

            raise Exception("NDS id 不能为空")

        if not date_type:

            raise Exception("Date type 不能为空")
        if not file_paths:

            raise Exception("file_paths 不能为空")

        response = await self.server.post("ndsfiles/filter", json={

            "ndsId": ndsId,

            "data_type": date_type,

            "file_paths": file_paths

        })

        if response.get("code") == 200:

            return response.get("data")
        else:

            raise Exception(f"获取文件失败: {json.dumps(response, ensure_ascii=False)}")
        





class Gateway:

    def __init__(self, gateway, client_id: str|None=None):

        self.client_id = client_id or uuid4().hex

        self.gateway_ws_url = f"ws://{gateway.get('host')}:{gateway.get('port')}/v1/nds/ws/"

        self.ws_client = WebSocketClient(self.gateway_ws_url, self.client_id)
        

    async def connect(self):

        await self.ws_client.connect()
        

    async def disconnect(self):

        await self.ws_client.close()
    

    async def is_connected(self):

        return await self.ws_client.is_connected()
    

    async def scan_nds(self, nds: str, path: str, filter: str):
    

        try:

            response = await self.ws_client.send_request(

                api="scan", 

                params={

                    "nds_id": nds, 

                    "path": path,
                    "filter": filter

                }
            )

            return response

        except Exception as e:

            return e


    async def zip_info(self, nds: str, path: str):

        try:

            response = await self.ws_client.send_request(

                api="zip_info", 

                params={

                    "nds_id": nds, 

                    "path": path

                }
            )

            return response

        except Exception as e:

            return e


    async def read(self, nds: str, path: str, header_offset: int, size: int):

        try:

            response = await self.ws_client.send_request(

                api="read", 

                params={

                    "nds_id": nds, 

                    "path": path,

                    "header_offset": header_offset,

                    "size": size

                }
            )

            return response

        except Exception as e:

            return e
    
    