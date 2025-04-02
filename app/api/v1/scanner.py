from fastapi import APIRouter
from app.api.deps import response_wrapper
from app.core.scanner import start


api_router = APIRouter(tags=["Scanner API"])

@api_router.get("/control/start")
@response_wrapper
async def control_start():
    """
    启动Scanner
    """
    return await start()
