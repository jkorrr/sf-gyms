import { createClient, type SupabaseClient } from "@supabase/supabase-js";

import { getSupabaseConfig, type SupabaseConfigStatus } from "./config";

let client: SupabaseClient | null | undefined;
let initializationFailed = false;

export function getSupabaseClient(): SupabaseClient | null {
  if (client !== undefined) return client;

  const result = getSupabaseConfig();
  if (result.status !== "configured") {
    client = null;
    return client;
  }

  try {
    client = createClient(result.config.url, result.config.publishableKey, {
      auth: {
        flowType: "pkce",
        // The static page exchanges the PKCE code explicitly after clearing
        // it from the URL, so Supabase must not consume the same callback a
        // second time automatically.
        detectSessionInUrl: false,
        persistSession: true,
        autoRefreshToken: true,
      },
    });
  } catch {
    // A malformed build-time value should degrade to demo mode rather than
    // preventing the static directory from rendering.
    initializationFailed = true;
    client = null;
  }
  return client;
}

export function getSupabaseStatus(): { status: SupabaseConfigStatus; message: string } {
  const result = getSupabaseConfig();
  if (initializationFailed) {
    return {
      status: "invalid",
      message: "Demo mode is active. Supabase could not initialize from the supplied public configuration. Check the project URL and publishable key; browsing and local saves still work.",
    };
  }
  return { status: result.status, message: result.message };
}
