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
