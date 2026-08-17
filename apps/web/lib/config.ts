export const basePath = process.env.NEXT_PUBLIC_BASE_PATH ?? "";

export function appOrigin(): string {
  if (typeof window === "undefined") return "";
  return `${window.location.origin}${basePath}`;
}
