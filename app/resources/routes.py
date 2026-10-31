"""Include all the other routes into one router."""

from fastapi import APIRouter

from app.config.settings import get_settings
from app.resources import (
    api_key,
    auth,
    heartbeat,
    home,
    monitor,
    notification,
    queue,
    registration,
    run_dashboard,
    user,
)

api_router = APIRouter(prefix=get_settings().api_root)

api_router.include_router(user.router)
api_router.include_router(auth.router)
api_router.include_router(api_key.router)
api_router.include_router(heartbeat.router)
api_router.include_router(registration.router)
api_router.include_router(monitor.router)
api_router.include_router(notification.router)
api_router.include_router(run_dashboard.router)
api_router.include_router(queue.router)

if not get_settings().no_root_route:
    api_router.include_router(home.router)
