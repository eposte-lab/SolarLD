import type { TerritoryType } from './enums';

export interface Territory {
  id: string;
  tenant_id: string;
  type: TerritoryType;
  code: string;
  name: string;
  bbox: { ne: { lat: number; lng: number }; sw: { lat: number; lng: number } } | null;
  excluded: boolean;
  priority: number;
  created_at: string;
  updated_at: string;
}
