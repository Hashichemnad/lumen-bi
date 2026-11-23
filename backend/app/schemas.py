import re
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from app.models import Role

AttributeValue = str | int | float | bool | list[str | int | float]

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _normalize_email(value: str) -> str:
    """Lenient check that also accepts internal domains such as name@company.local."""
    value = value.strip().lower()
    if len(value) > 320 or not _EMAIL_RE.match(value):
        raise ValueError("Enter a valid email address")
    return value


Email = Annotated[str, AfterValidator(_normalize_email)]


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------- auth ----------
class LoginIn(BaseModel):
    email: Email
    password: str


class PasswordChangeIn(BaseModel):
    current_password: str
    new_password: str


class ForgotPasswordIn(BaseModel):
    email: Email


class ResetPasswordIn(BaseModel):
    token: str
    new_password: str


class LogoutOut(BaseModel):
    logout_url: str | None = None


# ---------- single sign-on ----------
ProviderType = Literal["entra", "uaepass", "oidc"]


class ProviderPublic(ORM):
    """What the login page needs: no settings."""

    id: int
    type: str
    name: str


class ProviderIn(BaseModel):
    type: ProviderType
    name: str = Field(min_length=1, max_length=100)
    enabled: bool = False
    client_id: str = ""
    client_secret: str | None = None  # None = keep the stored secret
    config: dict[str, Any] = {}
    attribute_map: dict[str, str] = {}
    group_map: dict[str, str] = {}
    auto_create: bool = False
    default_role: Role = Role.viewer
    allowed_domains: list[str] = []
    link_by_email: bool = True


class ProviderOut(ORM):
    id: int
    type: str
    name: str
    enabled: bool
    client_id: str
    has_secret: bool = False
    config: dict[str, Any]
    attribute_map: dict[str, str]
    group_map: dict[str, str]
    auto_create: bool
    default_role: Role
    allowed_domains: list[str]
    link_by_email: bool
    callback_url: str = ""


class IdentityOut(BaseModel):
    provider_id: int
    provider_name: str
    provider_type: str
    email: str
    last_login_at: datetime | None


# ---------- groups ----------
class GroupRef(ORM):
    id: int
    name: str


class GroupIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""


class GroupOut(ORM):
    id: int
    name: str
    description: str
    source: str
    created_at: datetime
    member_count: int = 0


class GroupMembersIn(BaseModel):
    user_ids: list[int]


# ---------- users ----------
class UserOut(ORM):
    id: int
    email: str
    name: str
    role: Role
    attributes: dict[str, Any]
    auth_provider: str
    is_active: bool
    locked: bool = False
    last_login_at: datetime | None
    created_at: datetime
    groups: list[GroupRef] = []
    sign_in_methods: list[IdentityOut] = []
    preferences: dict[str, Any] = {}


class UserCreate(BaseModel):
    email: Email
    name: str = ""
    role: Role = Role.viewer
    password: str | None = None  # None -> SSO-only user, or set later via reset email
    attributes: dict[str, AttributeValue] = {}
    group_ids: list[int] = []
    send_invite: bool = False


class UserUpdate(BaseModel):
    name: str | None = None
    role: Role | None = None
    is_active: bool | None = None
    attributes: dict[str, AttributeValue] | None = None
    group_ids: list[int] | None = None
    password: str | None = None
    unlock: bool | None = None


class UserImportResult(BaseModel):
    created: int
    updated: int
    errors: list[str]


class Page[T](BaseModel):
    items: list[T]
    total: int


class AuditOut(ORM):
    id: int
    created_at: datetime
    user_id: int | None
    user_email: str
    action: str
    object_type: str
    object_id: str
    ip: str
    details: dict[str, Any]


# ---------- data: connections, datasets, sync runs ----------
class ConnectionTypeOut(BaseModel):
    type: str
    label: str
    supports_incremental: bool
    supports_sql: bool
    secret_fields: list[str]


class ConnectionIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    type: str
    config: dict[str, Any] = {}
    secrets: dict[str, str] | None = None  # None = keep stored secrets


class ConnectionTestIn(ConnectionIn):
    connection_id: int | None = None  # reuse stored secrets of an existing connection when testing edits


class ConnectionOut(ORM):
    id: int
    name: str
    type: str
    config: dict[str, Any]
    secrets_set: list[str] = []
    owner_id: int | None
    owner_name: str = ""
    dataset_count: int = 0
    created_at: datetime
    updated_at: datetime


class ObjectOut(BaseModel):
    name: str
    kind: str
    schema_: str | None = Field(default=None, alias="schema")
    label: str | None = None


class ColumnOut(BaseModel):
    name: str
    type: str


class DatasetIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    connection_id: int | None = None  # not used by joined/combined datasets
    source: dict[str, Any]
    sync_mode: Literal["full", "incremental"] = "full"
    incremental_column: str | None = None
    primary_key: list[str] = []
    schedule: str | None = None  # 5-field cron, interpreted in the server's time zone


class DatasetRef(BaseModel):
    id: int
    name: str


class DatasetOut(ORM):
    id: int
    name: str
    description: str
    kind: str
    connection_id: int | None
    parents: list[DatasetRef] = []
    dependents: list[DatasetRef] = []
    connection_name: str = ""
    connection_type: str = ""
    source: dict[str, Any]
    sync_mode: str
    incremental_column: str | None
    primary_key: list[str]
    schedule: str | None
    next_run_at: datetime | None
    current_version: int
    row_count: int
    columns: list[ColumnOut]
    last_synced_at: datetime | None
    last_status: str
    owner_id: int | None
    owner_name: str = ""
    active_run: "SyncRunOut | None" = None
    updated_at: datetime


class SyncRunOut(ORM):
    id: int
    dataset_id: int
    trigger: str
    status: str
    mode: str
    version: int | None
    rows_read: int
    rows_total: int
    error: str | None
    details: dict[str, Any]
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class PreviewOut(BaseModel):
    columns: list[str]
    rows: list[list[Any]]
    version: int
    restricted: bool = False  # row-level security hid some rows from this user


# ---------- semantic layer ----------
class FieldOut(ORM):
    id: int
    name: str
    kind: str
    label: str
    description: str
    role: str
    data_type: str
    format: dict[str, Any]
    default_agg: str
    hidden: bool
    formula: str | None
    config: dict[str, Any]
    is_aggregate: bool
    error: str | None
    missing: bool
    position: int
    used_by: list[str] = []


class FieldCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    kind: Literal["calculated", "group", "bin"] = "calculated"
    formula: str | None = None
    config: dict[str, Any] = {}
    label: str | None = None
    description: str = ""
    role: Literal["dimension", "measure"] | None = None
    format: dict[str, Any] | None = None


class FieldUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)  # calculated fields only
    label: str | None = None
    description: str | None = None
    role: Literal["dimension", "measure"] | None = None
    format: dict[str, Any] | None = None
    default_agg: str | None = None
    hidden: bool | None = None
    formula: str | None = None
    config: dict[str, Any] | None = None


class FieldOrderIn(BaseModel):
    ids: list[int]


class FormulaCheckIn(BaseModel):
    formula: str
    field_name: str | None = None  # the field being edited (so it may not refer to itself)
    kind: Literal["calculated", "group", "bin"] = "calculated"
    config: dict[str, Any] = {}


class FormulaCheckOut(BaseModel):
    ok: bool
    type: str | None = None
    is_aggregate: bool = False
    error: str | None = None
    pos: int | None = None
    sample: list[Any] = []


class HierarchyIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    levels: list[str]


class HierarchyOut(ORM):
    id: int
    name: str
    levels: list[str]


class ParameterIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = ""
    data_type: Literal["number", "integer", "string", "date", "boolean"] = "number"
    default_value: Any = None
    allowed_values: list[Any] = []


class ParameterOut(BaseModel):
    id: int
    name: str
    description: str
    data_type: str
    default_value: Any
    allowed_values: list[Any]


# ---------- query engine and row-level security ----------
class RlsRuleIn(BaseModel):
    name: str = Field("", max_length=200)
    kind: Literal["attribute", "entitlement"] = "attribute"
    config: dict[str, Any]
    exempt_groups: list[int] = []
    enabled: bool = True
    description: str = ""


class RlsRuleOut(ORM):
    id: int
    dataset_id: int
    name: str
    kind: str
    config: dict[str, Any]
    exempt_groups: list[int]
    enabled: bool
    description: str
    summary: str = ""
    exempt_group_names: list[str] = []


class InheritedRuleOut(BaseModel):
    rule: RlsRuleOut
    dataset: DatasetRef
    mapped: bool  # False: the rule's field is not in this dataset, so it blocks every row


class AccessIn(BaseModel):
    everyone: bool = False
    user_ids: list[int] = []
    group_ids: list[int] = []


class AccessOut(AccessIn):
    users: list[dict[str, Any]] = []
    groups: list[dict[str, Any]] = []


class SecurityOut(BaseModel):
    rules: list[RlsRuleOut]
    inherited: list[InheritedRuleOut]
    access: AccessOut
    attributes: list[str]  # attribute names in use by users, for the rule editor


class SecurityCheckOut(BaseModel):
    user: dict[str, Any]
    has_access: bool
    bypass: bool
    rules: list[dict[str, Any]]
    visible_rows: int | None
    total_rows: int


class CatalogDatasetOut(BaseModel):
    id: int
    name: str
    description: str
    row_count: int
    last_synced_at: datetime | None
    restricted: bool


# ---------- charts ----------
class ChartIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field("", max_length=2000)
    dataset_id: int
    config: dict[str, Any]
    query: dict[str, Any]
    workspace_id: int | None = None  # used when the chart is created


class ChartOut(ORM):
    id: int
    name: str
    description: str
    dataset_id: int
    dataset_name: str = ""
    owner_id: int | None
    owner_name: str = ""
    config: dict[str, Any]
    query: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    can_edit: bool = False
    workspace_id: int | None = None
    workspace_name: str = ""
    level: str = "manage"  # view | edit | manage


class MoveIn(BaseModel):
    """Put a dashboard or chart in a workspace (None: back to personal)."""

    workspace_id: int | None = None


# ---------- dashboards ----------
class DashboardIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field("", max_length=2000)
    config: dict[str, Any]
    workspace_id: int | None = None  # used when the dashboard is created


class DashboardSummary(BaseModel):
    id: int
    name: str
    description: str
    owner_id: int | None
    owner_name: str
    updated_at: datetime
    favorite: bool
    level: str  # view | edit | manage
    shared: bool
    tiles: int
    workspace_id: int | None = None
    workspace_name: str = ""
    layout: list[dict[str, Any]] = []  # first tab's tiles: kind, x, y, w, h


class ShareEntry(BaseModel):
    id: int
    level: Literal["view", "edit"] = "view"


class DashboardSharing(BaseModel):
    everyone: Literal["none", "view", "edit"] = "none"
    users: list[ShareEntry] = []
    groups: list[ShareEntry] = []


class DashboardQueryIn(BaseModel):
    item: str
    format: Literal["csv", "xlsx"] = "csv"  # exports only
    filters: list[dict[str, Any]] = []
    params: dict[str, Any] = {}
    drill: list[dict[str, Any]] = []
    rows: bool = False
    limit: int | None = Field(None, ge=1, le=50_000)


# ---------- workspaces ----------
class WorkspaceIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field("", max_length=2000)


class WorkspaceOut(BaseModel):
    id: int
    name: str
    description: str
    my_role: str  # viewer | editor | manager
    dashboards: int = 0
    charts: int = 0
    members: int = 0
    created_at: datetime


class WorkspaceMemberIn(BaseModel):
    principal: Literal["user", "group"]
    id: int
    role: Literal["viewer", "editor", "manager"] = "viewer"


class WorkspaceMemberOut(WorkspaceMemberIn):
    name: str = ""
    email: str = ""
