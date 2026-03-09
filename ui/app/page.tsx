import Link from "next/link";
import { getEvents } from "@/lib/events";

export default async function EventsPage() {
  const eventIds = await getEvents();

  return (
    <div style={{ maxWidth: 700, margin: "0 auto", padding: "32px 24px" }}>
      <div style={{ marginBottom: 20 }}>
        <h1
          style={{
            fontFamily: "monospace",
            fontSize: 11,
            letterSpacing: "0.09em",
            color: "#4a5f74",
            textTransform: "uppercase",
          }}
        >
          Agent Events
        </h1>
        <div
          style={{
            fontFamily: "monospace",
            fontSize: 10,
            color: "#4a5f74",
            marginTop: 4,
          }}
        >
          {eventIds.length} event{eventIds.length !== 1 ? "s" : ""}
        </div>
      </div>

      {eventIds.length === 0 ? (
        <div
          style={{
            background: "#0d1117",
            border: "1px solid #1e2a36",
            borderRadius: 6,
            padding: "40px 24px",
            textAlign: "center",
            fontFamily: "monospace",
            fontSize: 12,
            color: "#4a5f74",
          }}
        >
          No events found.
        </div>
      ) : (
        <div
          style={{
            background: "#0d1117",
            border: "1px solid #1e2a36",
            borderRadius: 6,
            overflow: "hidden",
          }}
        >
          <div
            style={{
              padding: "9px 16px",
              borderBottom: "1px solid #1e2a36",
              fontFamily: "monospace",
              fontSize: 9,
              color: "#4a5f74",
              letterSpacing: "0.09em",
              textTransform: "uppercase",
            }}
          >
            Event ID
          </div>
          {eventIds.map((event, i) => {
            const eventId =
              typeof event === "string" || typeof event === "number"
                ? event
                : ((event as any).id ?? i);
            return (
              <Link
                key={eventId}
                href={`/events/${eventId}`}
                className="event-row"
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  padding: "12px 16px",
                  textDecoration: "none",
                  borderTop: i > 0 ? "1px solid #1e2a36" : undefined,
                }}
              >
                <code
                  style={{
                    fontFamily: "monospace",
                    fontSize: 13,
                    color: "#f0a500",
                  }}
                >
                  {eventId}
                </code>
                <span
                  style={{
                    fontFamily: "monospace",
                    fontSize: 12,
                    color: "#4a5f74",
                  }}
                >
                  →
                </span>
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}
