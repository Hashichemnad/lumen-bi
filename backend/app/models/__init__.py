"""All ORM models. Import from here so Alembic sees every table."""

from app.models.audit import AuditLog
from app.models.boundary import MapBoundary
from app.models.chart import Chart
from app.models.dashboard import Dashboard, DashboardFavorite, DashboardShare
from app.models.data import Connection, Dataset, SyncRun
from app.models.identity import IdentityProvider, UserIdentity
from app.models.security import DatasetShare, RlsRule
from app.models.semantic import DatasetField, Hierarchy, Parameter
from app.models.settings import AppSetting
from app.models.user import Group, PasswordResetToken, Role, User, user_groups
from app.models.workspace import Workspace, WorkspaceMember

__all__ = [
    "AppSetting",
    "AuditLog",
    "Chart",
    "Dashboard",
    "DashboardFavorite",
    "DashboardShare",
    "Connection",
    "Dataset",
    "DatasetField",
    "DatasetShare",
    "Group",
    "Hierarchy",
    "IdentityProvider",
    "MapBoundary",
    "Parameter",
    "PasswordResetToken",
    "RlsRule",
    "Role",
    "SyncRun",
    "User",
    "UserIdentity",
    "Workspace",
    "WorkspaceMember",
    "user_groups",
]
