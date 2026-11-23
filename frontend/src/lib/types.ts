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

export interface DatasetRef {
  id: number;
  name: string;
}

export interface JoinSpec {
  base: number;
  joins: { dataset: number; type: "left" | "inner" | "full"; alias: string; on: { left: string; right: string }[] }[];
}

export interface UnionSpec {
  datasets: number[];
  source_column: string;
}

export interface Dataset {
  id: number;
  name: string;
  description: string;
  kind: "source" | "join" | "union";
  parents: DatasetRef[];
  dependents: DatasetRef[];
  connection_id: number | null;
  connection_name: string;
  connection_type: string;
  source: { object?: string; sql?: string; join?: JoinSpec; union?: UnionSpec };
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
  restricted: boolean;
}

export interface RlsRule {
  id: number;
  dataset_id: number;
  name: string;
  kind: "attribute" | "entitlement";
  config: {
    field: string;
    attribute?: string;
    ignore_case?: boolean;
    dataset?: number;
    user_column?: string;
    value_column?: string;
    match?: string;
    all_value?: string | null;
  };
  exempt_groups: number[];
  exempt_group_names: string[];
  enabled: boolean;
  description: string;
  summary: string;
}

export interface Principal {
  id: number;
  name: string;
  email?: string;
  role?: string;
}

export interface DatasetAccess {
  everyone: boolean;
  user_ids: number[];
  group_ids: number[];
  users: Principal[];
  groups: Principal[];
}

export interface DatasetSecurity {
  rules: RlsRule[];
  inherited: { rule: RlsRule; dataset: DatasetRef; mapped: boolean }[];
  access: DatasetAccess;
  attributes: string[];
}

export interface RuleCheck {
  rule_id: number;
  name: string;
  inherited_from: DatasetRef | null;
  status: "applies" | "exempt" | "all" | "blocked";
  values: unknown[];
  value_count: number;
  reason: string;
}

export interface SecurityCheck {
  user: Principal;
  has_access: boolean;
  bypass: boolean;
  rules: RuleCheck[];
  visible_rows: number | null;
  total_rows: number;
}

export interface QueryColumn {
  key: string;
  label: string;
  field: string;
  role: "dimension" | "measure";
  type: string;
  format: FieldFormat;
  agg: string | null;
  grain: string | null;
  part: string | null;
  calc: string | null;
}

export interface QueryResult {
  columns: QueryColumn[];
  rows: unknown[][];
  totals: unknown[] | null;
  truncated: boolean;
  row_count: number;
  restricted: boolean;
  cached: boolean;
  ms: number;
}

// ---------- model ----------
export type FieldKind = "column" | "calculated" | "group" | "bin";

export interface FieldFormat {
  type: "number" | "currency" | "percent" | "date" | "datetime" | "text";
  decimals?: number;
  currency?: string;
  grain?: string | null;
  plain?: boolean;
}

export interface GroupConfig {
  source: string;
  groups: { name: string; values: (string | number)[] }[];
  other?: string;
}

export interface Field {
  id: number;
  name: string;
  kind: FieldKind;
  label: string;
  description: string;
  role: "dimension" | "measure";
  data_type: string;
  format: FieldFormat;
  default_agg: string;
  hidden: boolean;
  formula: string | null;
  config: Partial<GroupConfig> & { size?: number };
  is_aggregate: boolean;
  error: string | null;
  missing: boolean;
  position: number;
  used_by: string[];
}

export interface FormulaCheck {
  ok: boolean;
  type: string | null;
  is_aggregate: boolean;
  error: string | null;
  pos: number | null;
  sample: unknown[];
}

export interface Hierarchy {
  id: number;
  name: string;
  levels: string[];
}

export interface Parameter {
  id: number;
  name: string;
  description: string;
  data_type: "number" | "integer" | "string" | "date" | "boolean";
  default_value: unknown;
  allowed_values: unknown[];
}
