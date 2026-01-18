const DEFAULT_API_BASE = `http://${window.location.hostname}:8000`;

export const API_BASE =
  (import.meta as any).env?.VITE_API_BASE || DEFAULT_API_BASE;

export function sseUrl(jobId: string) {
  return `${API_BASE}/progress/${jobId}`;
}

export async function startIdentify(file: File): Promise<{ job_id: string }> {
  const form = new FormData();
  form.append("file", file);

  const r = await fetch(`${API_BASE}/identify_async`, {
    method: "POST",
    body: form,
  });

  if (!r.ok) {
    const text = await r.text();
    throw new Error(text || "Failed to start scan");
  }

  return r.json();
}

export async function sendFeedback(payload: {
  image_hash: string;
  correct: boolean;
  chosen_variant_url?: string;
}) {
  const r = await fetch(`${API_BASE}/feedback`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  if (!r.ok) throw new Error("Feedback failed");
  return r.json();
}

export async function fetchHistory(variantUrl: string) {
  const r = await fetch(`${API_BASE}/price_history?url=${encodeURIComponent(variantUrl)}`);
  if (!r.ok) throw new Error("History fetch failed");
  return r.json();
}
