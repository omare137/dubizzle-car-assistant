import { API_BASE_URL } from "@/config";

export interface ApiCar {
  id: number;
  make: string;
  model: string;
  year: number;
  title: string;
  price_aed_cash: number | null;
  price_aed_monthly: number | null;
  price_unlisted: boolean;
  photo_url: string;
}

export interface BookingPromptData {
  car_id: number;
  day: string;
  time: string;
}

export interface SessionResponse {
  session_id: string;
  user: { id: number; name: string } | null;
  returning_user: boolean;
  profile_summary: string | null;
}

export interface ChatResponse {
  session_id: string;
  reply: string;
  cars: ApiCar[];
  booking_prompt: BookingPromptData | null;
}

export interface InventorySearchResponse {
  results: ApiCar[];
  count: number;
}

export class ApiError extends Error {
  status: number;
  detail: string;
  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
    this.detail = detail;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE_URL}${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...(init?.headers || {}),
      },
    });
  } catch (e) {
    throw new ApiError(0, e instanceof Error ? e.message : "Network error");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      if (body?.detail) detail = body.detail;
    } catch {
      /* noop */
    }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

export function createSession(username?: string) {
  const body = username && username.trim() ? { username: username.trim() } : {};
  return request<SessionResponse>("/session", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function sendChat(session_id: string, message: string) {
  return request<ChatResponse>("/chat", {
    method: "POST",
    body: JSON.stringify({ session_id, message }),
  });
}

export function endSession(session_id: string) {
  return request<{ session_id: string; ended: true }>(
    `/session/${encodeURIComponent(session_id)}/end`,
    { method: "POST" },
  );
}

export function searchInventory(params: {
  make?: string;
  model?: string;
  year_min?: number;
  year_max?: number;
  price_max?: number;
  keywords?: string;
  limit?: number;
}) {
  const q = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "") q.set(k, String(v));
  });
  const suffix = q.toString() ? `?${q.toString()}` : "";
  return request<InventorySearchResponse>(`/inventory/search${suffix}`);
}
