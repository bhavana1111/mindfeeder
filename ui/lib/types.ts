export interface OrderItem {
  qty: number;
  sku: string;
  price: number;
}

export interface Order {
  id: number;
  total: number;
  currency: string;
  items: OrderItem[];
  shipping_address: { zip: string; country: string };
  customer_note: string;
  email: string;
}

export interface EventPayload {
  event_type: string;
  source: string;
  order: Order;
}

export interface AppEvent {
  id: string;
  source: string;
  orderEmail: string;
  createdAt: string;
  status: string;
  orderId: number;
  payload: EventPayload;
  eventType: string;
}

export interface AgentOutput {
  model: string;
  createdAt: string;
  outputJson: Record<string, unknown>;
  runType: string;
}

export interface AuditEntry {
  id: string;
  estimatedCostUsd: number;
  triggeredBy: string;
  totalTokens: number;
  model: string;
  triggeredAt: string;
  agentsRun: string[];
  action: string;
}

export interface EventDetail {
  event: AppEvent;
  outputs: Record<string, AgentOutput>;
  audit_log: AuditEntry[];
}
