import { EventDetail } from "./types";

const BASE_URL = "https://mindfeeder-triage-213036786657.us-central1.run.app";
export async function getEvents(): Promise<string[]> {
  try {
    const res = await fetch(`${BASE_URL}/events`, { cache: "no-store" });
    if (!res.ok) return [];
    const data = await res.json();
    return data.events ?? [];
  } catch {
    return [];
  }
}

export async function getEvent(id: string): Promise<EventDetail | undefined> {
  try {
    const res = await fetch(`${BASE_URL}/events/${id}`, { cache: "no-store" });
    if (!res.ok) return undefined;
    return (await res.json()) as EventDetail;
  } catch {
    return undefined;
  }
}
