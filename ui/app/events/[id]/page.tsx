import Link from "next/link";
import { notFound } from "next/navigation";
import { getEvent } from "@/lib/events";
import { AgentOutput, AuditEntry } from "@/lib/types";

function fmt(iso: string) {
  return new Date(iso).toLocaleString("en-US", {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

function colorizeJson(val: unknown): string {
  return JSON.stringify(val, null, 2)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"([^"]+)":/g, '<span class="k">"$1"</span>:')
    .replace(/: "([^"]*)"/g, ': <span class="s">"$1"</span>')
    .replace(/: (-?\d+\.?\d*)/g, ': <span class="n">$1</span>')
    .replace(/: (true|false)/g, ': <span class="b">$1</span>')
    .replace(/: null/g, ': <span class="z">null</span>');
}

const mono: React.CSSProperties = { fontFamily: "monospace" };

function Section({
  title,
  children,
  right,
}: {
  title: string;
  children: React.ReactNode;
  right?: React.ReactNode;
}) {
  return (
    <div
      style={{
        border: "1px solid #1e2a36",
        borderRadius: 6,
        overflow: "hidden",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "10px 16px",
          background: "#0d1117",
          borderBottom: "1px solid #1e2a36",
        }}
      >
        <span
          style={{
            ...mono,
            fontSize: 9,
            letterSpacing: "0.1em",
            color: "#4a5f74",
            textTransform: "uppercase" as const,
          }}
        >
          {title}
        </span>
        {right}
      </div>
      <div style={{ background: "#0a0e14" }}>{children}</div>
    </div>
  );
}

function Chip({
  children,
  color = "#4a5f74",
  bg = "rgba(74,95,116,0.12)",
  border = "rgba(74,95,116,0.25)",
}: {
  children: React.ReactNode;
  color?: string;
  bg?: string;
  border?: string;
}) {
  return (
    <span
      style={{
        ...mono,
        fontSize: 10,
        fontWeight: 600,
        letterSpacing: "0.06em",
        padding: "2px 8px",
        borderRadius: 3,
        color,
        background: bg,
        border: `1px solid ${border}`,
      }}
    >
      {children}
    </span>
  );
}

function KV({
  label,
  value,
  valueColor,
}: {
  label: string;
  value: React.ReactNode;
  valueColor?: string;
}) {
  return (
    <div
      style={{
        display: "flex",
        gap: 10,
        alignItems: "flex-start",
        padding: "7px 0",
        borderBottom: "1px solid #111820",
      }}
    >
      <span
        style={{
          ...mono,
          fontSize: 10,
          color: "#4a5f74",
          minWidth: 140,
          paddingTop: 1,
        }}
      >
        {label}
      </span>
      <span
        style={{
          ...mono,
          fontSize: 11,
          color: valueColor ?? "#c8d8e8",
          flex: 1,
          wordBreak: "break-all" as const,
        }}
      >
        {value}
      </span>
    </div>
  );
}

function JsonPre({ data }: { data: unknown }) {
  return (
    <pre
      style={{
        ...mono,
        fontSize: 12,
        lineHeight: 1.7,
        color: "#c8d8e8",
        padding: "14px 16px",
        margin: 0,
        overflowX: "auto",
        whiteSpace: "pre-wrap",
        wordBreak: "break-word",
      }}
      dangerouslySetInnerHTML={{ __html: colorizeJson(data) }}
    />
  );
}

const AGENT_META: Record<
  string,
  { label: string; color: string; bg: string; border: string; icon: string }
> = {
  fraud_risk: {
    label: "Fraud Risk",
    icon: "🛡",
    color: "#f87171",
    bg: "rgba(248,113,113,0.08)",
    border: "rgba(248,113,113,0.25)",
  },
  fulfillment_note: {
    label: "Fulfillment Note",
    icon: "📦",
    color: "#60a5fa",
    bg: "rgba(96,165,250,0.08)",
    border: "rgba(96,165,250,0.25)",
  },
  support_reply: {
    label: "Support Reply",
    icon: "💬",
    color: "#34d399",
    bg: "rgba(52,211,153,0.08)",
    border: "rgba(52,211,153,0.25)",
  },
};

function FraudRiskCard({ out }: { out: AgentOutput }) {
  const d = out.outputJson as {
    risk_level: string;
    confidence: number;
    reasons: string[];
  };
  const levelColor =
    d.risk_level === "high"
      ? "#ef4444"
      : d.risk_level === "medium"
        ? "#f0a500"
        : "#22c55e";
  const pct = Math.round(d.confidence * 100);
  return (
    <div style={{ padding: 16 }}>
      <div style={{ display: "flex", gap: 12, marginBottom: 14 }}>
        <div
          style={{
            flex: 1,
            background: "#0d1117",
            border: "1px solid #1e2a36",
            borderRadius: 5,
            padding: "12px 16px",
          }}
        >
          <div
            style={{
              ...mono,
              fontSize: 9,
              color: "#4a5f74",
              letterSpacing: "0.08em",
              marginBottom: 4,
            }}
          >
            RISK LEVEL
          </div>
          <div
            style={{
              ...mono,
              fontSize: 20,
              fontWeight: 700,
              color: levelColor,
              textTransform: "uppercase" as const,
            }}
          >
            {d.risk_level}
          </div>
        </div>
        <div
          style={{
            flex: 1,
            background: "#0d1117",
            border: "1px solid #1e2a36",
            borderRadius: 5,
            padding: "12px 16px",
          }}
        >
          <div
            style={{
              ...mono,
              fontSize: 9,
              color: "#4a5f74",
              letterSpacing: "0.08em",
              marginBottom: 6,
            }}
          >
            CONFIDENCE
          </div>
          <div
            style={{ ...mono, fontSize: 20, fontWeight: 700, color: "#e8f4ff" }}
          >
            {pct}%
          </div>
          <div
            style={{
              marginTop: 6,
              height: 4,
              background: "#1e2a36",
              borderRadius: 2,
              overflow: "hidden",
            }}
          >
            <div
              style={{
                height: "100%",
                width: `${pct}%`,
                background: levelColor,
                borderRadius: 2,
              }}
            />
          </div>
        </div>
      </div>
      <div
        style={{
          ...mono,
          fontSize: 9,
          color: "#4a5f74",
          letterSpacing: "0.08em",
          marginBottom: 8,
        }}
      >
        REASONS
      </div>
      {d.reasons.map((r, i) => (
        <div
          key={i}
          style={{
            display: "flex",
            gap: 8,
            alignItems: "flex-start",
            marginBottom: 6,
          }}
        >
          <span style={{ color: "#f0a500", marginTop: 1 }}>›</span>
          <span style={{ ...mono, fontSize: 12, color: "#c8d8e8" }}>{r}</span>
        </div>
      ))}
    </div>
  );
}

function FulfillmentCard({ out }: { out: AgentOutput }) {
  const d = out.outputJson as {
    packing_notes: string;
    carrier_hint: string;
    priority: string;
  };
  const priColor = d.priority === "expedited" ? "#f0a500" : "#22c55e";
  return (
    <div style={{ padding: 16 }}>
      <div style={{ display: "flex", gap: 10, marginBottom: 14 }}>
        <Chip color={priColor} bg={`${priColor}18`} border={`${priColor}40`}>
          {d.priority.toUpperCase()}
        </Chip>
        <Chip
          color="#60a5fa"
          bg="rgba(96,165,250,0.1)"
          border="rgba(96,165,250,0.3)"
        >
          📮 {d.carrier_hint}
        </Chip>
      </div>
      <div
        style={{
          ...mono,
          fontSize: 9,
          color: "#4a5f74",
          letterSpacing: "0.08em",
          marginBottom: 8,
        }}
      >
        PACKING NOTES
      </div>
      <div
        style={{
          background: "#0d1117",
          border: "1px solid #1e2a36",
          borderRadius: 4,
          padding: "12px 14px",
          ...mono,
          fontSize: 12,
          color: "#c8d8e8",
          lineHeight: 1.6,
        }}
      >
        {d.packing_notes}
      </div>
    </div>
  );
}

function SupportReplyCard({ out }: { out: AgentOutput }) {
  const d = out.outputJson as {
    tone: string;
    subject: string;
    body: string;
    disclaimers: string[];
  };
  return (
    <div
      style={{ padding: 16, display: "flex", flexDirection: "column", gap: 12 }}
    >
      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <Chip>{d.tone.toUpperCase()}</Chip>
        <span style={{ ...mono, fontSize: 11, color: "#e8f4ff" }}>
          {d.subject}
        </span>
      </div>
      <div
        style={{
          background: "#0d1117",
          border: "1px solid #1e2a36",
          borderRadius: 4,
          padding: "14px 16px",
        }}
      >
        <div
          style={{
            ...mono,
            fontSize: 9,
            color: "#4a5f74",
            letterSpacing: "0.08em",
            marginBottom: 8,
          }}
        >
          BODY
        </div>
        <div
          style={{
            ...mono,
            fontSize: 12,
            color: "#c8d8e8",
            lineHeight: 1.7,
            whiteSpace: "pre-wrap",
          }}
        >
          {d.body}
        </div>
      </div>
      {d.disclaimers?.length > 0 && (
        <div>
          <div
            style={{
              ...mono,
              fontSize: 9,
              color: "#4a5f74",
              letterSpacing: "0.08em",
              marginBottom: 6,
            }}
          >
            DISCLAIMERS
          </div>
          {d.disclaimers.map((disc, i) => (
            <div
              key={i}
              style={{
                ...mono,
                fontSize: 10,
                color: "#4a5f74",
                marginBottom: 4,
                paddingLeft: 12,
                borderLeft: "2px solid #1e2a36",
              }}
            >
              {disc}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function AgentCard({ name, out }: { name: string; out: AgentOutput }) {
  const meta = AGENT_META[name] ?? {
    label: name,
    icon: "⚙",
    color: "#c8d8e8",
    bg: "rgba(200,216,232,0.08)",
    border: "rgba(200,216,232,0.2)",
  };
  return (
    <div
      style={{
        border: `1px solid ${meta.border}`,
        borderRadius: 6,
        overflow: "hidden",
        background: meta.bg,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "10px 16px",
          borderBottom: `1px solid ${meta.border}`,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontSize: 14 }}>{meta.icon}</span>
          <span
            style={{
              ...mono,
              fontSize: 12,
              fontWeight: 600,
              color: meta.color,
            }}
          >
            {meta.label}
          </span>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <Chip>{out.runType}</Chip>
          <span style={{ ...mono, fontSize: 10, color: "#4a5f74" }}>
            {fmt(out.createdAt)}
          </span>
          <span style={{ ...mono, fontSize: 10, color: "#4a5f74" }}>
            {out.model}
          </span>
        </div>
      </div>
      {name === "fraud_risk" && <FraudRiskCard out={out} />}
      {name === "fulfillment_note" && <FulfillmentCard out={out} />}
      {name === "support_reply" && <SupportReplyCard out={out} />}
      {!["fraud_risk", "fulfillment_note", "support_reply"].includes(name) && (
        <JsonPre data={out.outputJson} />
      )}
    </div>
  );
}

export default async function EventDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const detail = await getEvent(id);
  if (!detail) notFound();

  const { event: ev, outputs, audit_log } = detail;
  const audit = audit_log[0] as AuditEntry | undefined;
  const statusColor =
    ev.status === "complete"
      ? "#22c55e"
      : ev.status === "failed"
        ? "#ef4444"
        : "#f0a500";

  return (
    <div
      style={{
        maxWidth: 860,
        margin: "0 auto",
        padding: "28px 24px",
        display: "flex",
        flexDirection: "column",
        gap: 18,
      }}
    >
      <Link
        href="/"
        style={{
          ...mono,
          fontSize: 10,
          color: "#4a5f74",
          textDecoration: "none",
          letterSpacing: "0.06em",
        }}
      >
        ← ALL EVENTS
      </Link>

      {/* Header */}
      <div
        style={{
          background: "#0d1117",
          border: "1px solid #1e2a36",
          borderRadius: 6,
          padding: "18px 20px",
        }}
      >
        <div
          style={{
            display: "flex",
            flexWrap: "wrap",
            alignItems: "center",
            gap: 12,
            marginBottom: 12,
          }}
        >
          <code
            style={{ ...mono, fontSize: 15, fontWeight: 700, color: "#f0a500" }}
          >
            {ev.id}
          </code>
          <Chip
            color={statusColor}
            bg={`${statusColor}18`}
            border={`${statusColor}40`}
          >
            {ev.status.toUpperCase()}
          </Chip>
          <Chip
            color="#60a5fa"
            bg="rgba(96,165,250,0.1)"
            border="rgba(96,165,250,0.3)"
          >
            {ev.eventType}
          </Chip>
          <Chip>{ev.source}</Chip>
        </div>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "1fr 1fr 1fr",
            gap: "0 24px",
          }}
        >
          <KV label="Order ID" value={`#${ev.orderId}`} valueColor="#e8f4ff" />
          <KV label="Customer" value={ev.orderEmail} />
          <KV label="Created" value={fmt(ev.createdAt)} />
        </div>
      </div>

      {/* Order */}
      <Section title="Order">
        <div style={{ padding: 16 }}>
          <div
            style={{
              border: "1px solid #1e2a36",
              borderRadius: 4,
              overflow: "hidden",
              marginBottom: 14,
            }}
          >
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "1fr 80px 80px 80px",
                padding: "7px 12px",
                background: "#0d1117",
                borderBottom: "1px solid #1e2a36",
                ...mono,
                fontSize: 9,
                color: "#4a5f74",
                letterSpacing: "0.08em",
                textTransform: "uppercase" as const,
                gap: 8,
              }}
            >
              <span>SKU</span>
              <span style={{ textAlign: "right" }}>QTY</span>
              <span style={{ textAlign: "right" }}>PRICE</span>
              <span style={{ textAlign: "right" }}>LINE</span>
            </div>
            {ev.payload.order.items.map((item, i) => (
              <div
                key={i}
                style={{
                  display: "grid",
                  gridTemplateColumns: "1fr 80px 80px 80px",
                  padding: "9px 12px",
                  gap: 8,
                  alignItems: "center",
                  borderTop: i > 0 ? "1px solid #1e2a36" : undefined,
                }}
              >
                <code style={{ ...mono, fontSize: 12, color: "#f0a500" }}>
                  {item.sku}
                </code>
                <span
                  style={{
                    ...mono,
                    fontSize: 12,
                    color: "#c8d8e8",
                    textAlign: "right",
                  }}
                >
                  {item.qty}
                </span>
                <span
                  style={{
                    ...mono,
                    fontSize: 12,
                    color: "#c8d8e8",
                    textAlign: "right",
                  }}
                >
                  ${item.price.toFixed(2)}
                </span>
                <span
                  style={{
                    ...mono,
                    fontSize: 12,
                    color: "#e8f4ff",
                    textAlign: "right",
                  }}
                >
                  ${(item.qty * item.price).toFixed(2)}
                </span>
              </div>
            ))}
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "1fr 80px",
                padding: "9px 12px",
                borderTop: "1px solid #1e2a36",
                background: "rgba(240,165,0,0.04)",
              }}
            >
              <span
                style={{
                  ...mono,
                  fontSize: 11,
                  fontWeight: 600,
                  color: "#4a5f74",
                }}
              >
                TOTAL ({ev.payload.order.currency})
              </span>
              <span
                style={{
                  ...mono,
                  fontSize: 13,
                  fontWeight: 700,
                  color: "#22c55e",
                  textAlign: "right",
                }}
              >
                ${ev.payload.order.total.toFixed(2)}
              </span>
            </div>
          </div>
          <div
            style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}
          >
            <div
              style={{
                background: "#0d1117",
                border: "1px solid #1e2a36",
                borderRadius: 4,
                padding: "10px 12px",
              }}
            >
              <div
                style={{
                  ...mono,
                  fontSize: 9,
                  color: "#4a5f74",
                  letterSpacing: "0.08em",
                  marginBottom: 5,
                }}
              >
                SHIP TO
              </div>
              <div style={{ ...mono, fontSize: 12, color: "#c8d8e8" }}>
                {ev.payload.order.shipping_address.zip} ·{" "}
                {ev.payload.order.shipping_address.country}
              </div>
            </div>
            <div
              style={{
                background: "rgba(240,165,0,0.05)",
                border: "1px solid rgba(240,165,0,0.2)",
                borderRadius: 4,
                padding: "10px 12px",
              }}
            >
              <div
                style={{
                  ...mono,
                  fontSize: 9,
                  color: "#f0a500",
                  letterSpacing: "0.08em",
                  marginBottom: 5,
                }}
              >
                CUSTOMER NOTE
              </div>
              <div
                style={{
                  ...mono,
                  fontSize: 12,
                  color: "#e8f4ff",
                  fontStyle: "italic",
                }}
              >
                "{ev.payload.order.customer_note}"
              </div>
            </div>
          </div>
        </div>
      </Section>

      {/* Agent Outputs */}
      <Section
        title={`Agent Outputs — ${Object.keys(outputs).length} agents`}
        right={<Chip>initial_run</Chip>}
      >
        <div
          style={{
            padding: 12,
            display: "flex",
            flexDirection: "column",
            gap: 10,
          }}
        >
          {Object.entries(outputs).map(([name, out]) => (
            <AgentCard key={name} name={name} out={out} />
          ))}
        </div>
      </Section>

      {/* Audit Log */}
      {audit && (
        <Section title="Audit Log">
          <div style={{ padding: 16 }}>
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(4, 1fr)",
                gap: 10,
                marginBottom: 14,
              }}
            >
              {[
                {
                  label: "TOTAL TOKENS",
                  value: audit.totalTokens.toLocaleString(),
                  color: "#60a5fa",
                },
                {
                  label: "EST. COST (USD)",
                  value: `$${audit.estimatedCostUsd.toFixed(6)}`,
                  color: "#22c55e",
                },
                {
                  label: "TRIGGERED BY",
                  value: audit.triggeredBy,
                  color: "#c8d8e8",
                },
                { label: "ACTION", value: audit.action, color: "#c8d8e8" },
              ].map((m) => (
                <div
                  key={m.label}
                  style={{
                    background: "#0d1117",
                    border: "1px solid #1e2a36",
                    borderRadius: 4,
                    padding: "10px 12px",
                  }}
                >
                  <div
                    style={{
                      ...mono,
                      fontSize: 16,
                      fontWeight: 700,
                      color: m.color,
                    }}
                  >
                    {m.value}
                  </div>
                  <div
                    style={{
                      ...mono,
                      fontSize: 9,
                      color: "#4a5f74",
                      letterSpacing: "0.06em",
                      marginTop: 2,
                    }}
                  >
                    {m.label}
                  </div>
                </div>
              ))}
            </div>
            <div style={{ display: "flex", gap: 12, alignItems: "flex-start" }}>
              <div style={{ flex: 1 }}>
                <div
                  style={{
                    ...mono,
                    fontSize: 9,
                    color: "#4a5f74",
                    letterSpacing: "0.08em",
                    marginBottom: 6,
                  }}
                >
                  AGENTS RUN
                </div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                  {audit.agentsRun.map((a) => {
                    const m = AGENT_META[a];
                    return (
                      <Chip
                        key={a}
                        color={m?.color ?? "#c8d8e8"}
                        bg={m?.bg ?? "rgba(200,216,232,0.08)"}
                        border={m?.border ?? "rgba(200,216,232,0.2)"}
                      >
                        {m?.icon} {a}
                      </Chip>
                    );
                  })}
                </div>
              </div>
              <div>
                <div
                  style={{
                    ...mono,
                    fontSize: 9,
                    color: "#4a5f74",
                    letterSpacing: "0.08em",
                    marginBottom: 6,
                  }}
                >
                  MODEL
                </div>
                <Chip>{audit.model}</Chip>
              </div>
            </div>
            <div
              style={{ marginTop: 12, ...mono, fontSize: 10, color: "#4a5f74" }}
            >
              Triggered at {fmt(audit.triggeredAt)}
            </div>
          </div>
        </Section>
      )}
    </div>
  );
}
