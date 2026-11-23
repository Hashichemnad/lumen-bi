import type { ChartConfig } from "../chart/types";
import type { FieldFormat, Hierarchy } from "../types";

export interface Box {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface Tile {
  id: string;
  kind: "chart" | "text" | "image" | "web";
  chart_id?: number;
  title?: string;
  show_title?: boolean;
  text?: string;
  url?: string;
  alt?: string;
  fit?: "contain" | "cover";
  desktop: Box;
  phone?: Box | null;
}

export interface Tab {
  id: string;
  name: string;
  items: Tile[];
}

export type FilterControl = "dropdown" | "multi" | "search" | "date" | "range";

export interface DashFilter {
  id: string;
  label: string;
  kind: "values" | "range" | "relative" | "text";
  control: FilterControl;
  targets: { dataset: number; field: string }[];
  /** default value; people change it on the dashboard */
  value: FilterValue;
  grain?: "year" | "quarter" | "month" | "day" | null;
}

export type DateValue = { mode: "relative"; unit: "day" | "week" | "month" | "quarter" | "year"; n: number } | { mode: "range"; from: string | null; to: string | null };
export type FilterValue = unknown[] | { min: number | null; max: number | null } | DateValue | string | null;

export interface DashConfig {
  tabs: Tab[];
  filters: DashFilter[];
  options: { cross_filter: boolean; view_data: boolean; phone_layout: boolean };
}

export interface DashField {
  name: string;
  label: string;
  role: "dimension" | "measure";
  data_type: string;
  format: FieldFormat;
  is_aggregate: boolean;
  default_agg: string;
}

export interface DashDataset {
  id: number;
  name: string;
  fields: DashField[];
  hierarchies: Hierarchy[];
  parameters: { name: string; description: string; data_type: string; default_value: unknown; allowed_values: unknown[] }[];
}

export interface DashboardSummary {
  id: number;
  name: string;
  description: string;
  owner_id: number | null;
  owner_name: string;
  updated_at: string;
  favorite: boolean;
  level: "view" | "edit" | "manage";
  shared: boolean;
  tiles: number;
}

export interface Dashboard extends DashboardSummary {
  config: DashConfig;
  charts: Record<string, { id: number; name: string; dataset_id: number; config: ChartConfig; can_edit: boolean }>;
  datasets: Record<string, DashDataset>;
  can_edit: boolean;
  can_manage: boolean;
}

export const emptyDashboard = (): DashConfig => ({
  tabs: [{ id: "tab1", name: "Overview", items: [] }],
  filters: [],
  options: { cross_filter: true, view_data: true, phone_layout: false },
});
