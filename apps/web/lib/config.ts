export type SupabaseConfigStatus = "configured" | "missing" | "invalid";

export type SupabasePublicConfig = {
  url: string;
  publishableKey: string;
};

export type SupabaseConfigResult =
  | { status: "configured"; config: SupabasePublicConfig; message: string }
  | { status: "missing" | "invalid"; config: null; message: string };

export const basePath = (process.env.NEXT_PUBLIC_BASE_PATH ?? "").replace(/\/$/, "");

function looksLikePublishableKey(key: string): boolean {
  if (key.length < 20 || /\s/.test(key)) return false;

  const lowerKey = key.toLowerCase();
  if (lowerKey.includes("service_role") || lowerKey.includes("secret") || lowerKey.includes("your_key")) {
    return false;
  }

  // Supabase now issues sb_publishable_* keys. Accept the older anon JWT shape
  // too, because existing projects may still use that browser-safe key format.
  if (key.startsWith("sb_publishable_")) return true;
  return key.split(".").length === 3 && key.startsWith("eyJ");
}

function looksLikeSupabaseUrl(value: string): boolean {
  try {
    const parsed = new URL(value);
    const isLocal = parsed.hostname === "localhost" || parsed.hostname === "127.0.0.1";
    const isHttps = parsed.protocol === "https:";
    return (isHttps || (parsed.protocol === "http:" && isLocal))
      && !parsed.username
      && !parsed.password
      && !parsed.search
      && !parsed.hash
      && Boolean(parsed.hostname);
  } catch {
    return false;
  }
}

export function getSupabaseConfig(): SupabaseConfigResult {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL?.trim() ?? "";
  const publishableKey = process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY?.trim() ?? "";

  if (!url && !publishableKey) {
    return {
      status: "missing",
      config: null,
      message: "Demo mode is active. Google sign-in is unavailable because this build has no Supabase public configuration. Add NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY at build time; browsing and local saves still work.",
    };
  }

  if (!looksLikeSupabaseUrl(url) || !looksLikePublishableKey(publishableKey)) {
    return {
      status: "invalid",
      config: null,
      message: "Demo mode is active. Supabase configuration is incomplete or invalid. Check the public project URL and publishable key, and keep service-role, database, and Google client secrets out of the frontend.",
    };
  }

  return {
    status: "configured",
    config: { url, publishableKey },
    message: "Supabase is configured for Google sign-in. Saved gyms remain local in this prototype until cloud-save syncing is enabled for database-backed gym locations.",
  };
}

export function appOrigin(): string {
  if (typeof window === "undefined") return "";
  return `${window.location.origin}${basePath}`;
}

export function oauthRedirectUrl(): string {
  return `${appOrigin()}/`;
}
