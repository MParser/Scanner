from app.core.ws_client import WebSocketClient, WebSocketResponse
import asyncio

async def test_client(client_id: str):
    client = WebSocketClient("ws://127.0.0.1:10101/v1/nds/ws", client_id)
    try:
        await client.connect()
        
        # 测试扫描
        scan_response = await client.send_request(
            api="scan", 
            params={
                "nds_id": "2", 
                "path": "/MR/MRO/",
                "filter": "^/MR/MRO/[^/]+/[^/]+_MRO_[^/]+\\.zip$"
            }
        )
        print(f"[{client_id}] 扫描结果数量:", len(scan_response.data))
        
        # 测试获取ZIP信息
        zip_info = await client.send_request(
            api="zip_info", 
            params={
                "nds_id": "2", 
                "path": "/MR/MRO/2025-02-08/FDD-LTE_MRO_ZTE_OMC1_20250208000000.zip"
            }
        )
        print(f"[{client_id}] ZIP文件数量:", len(zip_info.data))
        # 测试读取数据
        read_response = await client.send_request(
            api="read", 
            params={
                "nds_id": "2", 
                "path": "/MR/MRO/2025-02-08/FDD-LTE_MRO_ZTE_OMC1_20250208000000.zip",
                "header_offset": 1542313,
                "size": 534019
            }
        )
        print(f"[{client_id}] 读取数据大小:", len(read_response.Bytes) if read_response.Bytes else 0)
        
    except WebSocketResponse as e:
        print(f"[{client_id}] 错误:", e)
    finally:
        await client.close()

async def main():
    # 创建多个客户端任务
    tasks = [
        test_client(f"client_{i}")
        for i in range(3)  # 创建3个客户端
    ]
    
    # 并发执行所有任务
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    asyncio.run(main())