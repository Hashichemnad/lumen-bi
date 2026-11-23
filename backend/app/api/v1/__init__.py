from fastapi import APIRouter, Depends

from app.api.deps import csrf_protect
from app.api.v1 import admin, auth, boundaries, charts, dashboards, data, groups, model, query, sso, users

api_router = APIRouter(prefix="/api/v1", dependencies=[Depends(csrf_protect)])
api_router.include_router(admin.router)
api_router.include_router(auth.router)
api_router.include_router(sso.router)
api_router.include_router(users.router)
api_router.include_router(groups.router)
api_router.include_router(data.router)
api_router.include_router(model.router)
api_router.include_router(query.router)
api_router.include_router(charts.router)
api_router.include_router(dashboards.router)
api_router.include_router(boundaries.router)
