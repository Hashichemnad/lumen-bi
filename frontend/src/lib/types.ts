export type Role = "viewer" | "explorer" | "creator" | "admin";
export const ROLES: { value: Role; label: string; help: string }[] = [
  { value: "viewer", label: "Viewer", help: "Views dashboards shared with them" },
  { value: "explorer", label: "Explorer", help: "Viewer + edit copies, ask AI, export" },
  { value: "creator", label: "Creator", help: "Explorer + connections, datasets, dashboards" },
  { value: "admin", label: "Admin", help: "Everything, including users and settings" },
];
export const ROLE_LEVEL: Record<Role, number> = { viewer: 0, explorer: 1, creator: 2, admin: 3 };

export type AttributeValue = string | number | boolean | (string | number)[];

export interface GroupRef {
  id: number;
  name: string;
}

export interface SignInMethod {
  provider_id: number;
  provider_name: string;
  provider_type: ProviderType;
  email: string;
  last_login_at: string | null;
}

export interface User {
  id: number;
  email: string;
  name: string;
  role: Role;
  attributes: Record<string, AttributeValue>;
  auth_provider: string;
  is_active: boolean;
  locked: boolean;
  last_login_at: string | null;
  created_at: string;
  groups: GroupRef[];
  sign_in_methods: SignInMethod[];
}

export interface Group {
  id: number;
  name: string;
  description: string;
  source: string;
  created_at: string;
  member_count: number;
}

export interface AuditEntry {
  id: number;
  created_at: string;
  user_id: number | null;
  user_email: string;
  action: string;
  object_type: string;
  object_id: string;
  ip: string;
  details: Record<string, unknown>;
}

export interface Page<T> {
  items: T[];
  total: number;
}

export type ProviderType = "entra" | "uaepass" | "oidc";

export interface ProviderPublic {
  id: number;
  type: ProviderType;
  name: string;
}

export interface Provider extends ProviderPublic {
  enabled: boolean;
  client_id: string;
  has_secret: boolean;
  config: Record<string, string>;
  attribute_map: Record<string, string>;
  group_map: Record<string, string>;
  auto_create: boolean;
  default_role: Role;
  allowed_domains: string[];
  link_by_email: boolean;
  callback_url: string;
}

// ---------- data ----------
export interface ConnectionType {
  type: string;
  label: string;
  supports_incremental: boolean;
  supports_sql: boolean;
  secret_fields: string[];
}

export interface Connection {
  id: number;
  name: string;
  type: string;
  config: Record<string, unknown>;
  secrets_set: string[];
  owner_id: number | null;
  owner_name: string;
  dataset_count: number;
  created_at: string;
  updated_at: string;
}

export interface DataObject {
  name: string;
  kind: string;
  schema: string | null;
  label: string | null;
}

export interface Column {
  name: string;
  type: string;
}

export interface SyncRun {
  id: number;
  dataset_id: number;
  trigger: string;
  status: "queued" | "running" | "success" | "failed";
  mode: string;
  version: number | null;
  rows_read: number;
  rows_total: number;
  error: string | null;
  details: { schema_changes?: { added?: string[]; removed?: string[]; retyped?: { name: string; from: string; to: string }[] }; note?: string };
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface Dataset {
  id: number;
  name: string;
  description: string;
  connection_id: number;
  connection_name: string;
  connection_type: string;
  source: { object?: string; sql?: string };
  sync_mode: "full" | "incremental";
  incremental_column: string | null;
  primary_key: string[];
  schedule: string | null;
  next_run_at: string | null;
  current_version: number;
  row_count: number;
  columns: Column[];
  last_synced_at: string | null;
  last_status: "never" | "success" | "failed";
  owner_id: number | null;
  owner_name: string;
  active_run: SyncRun | null;
  updated_at: string;
}

export interface Preview {
  columns: string[];
  rows: unknown[][];
  version: number;
}
