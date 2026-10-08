/**
 * A human name for a subscribed device, from its User-Agent: "iPhone · Home
 * Screen app", "Android · Chrome", "Mac · Safari". Shown on /onboarding and
 * stored as push_subscriptions.device_label. Best effort, never trusted.
 */
export function deviceLabel(userAgent: string, standalone: boolean): string {
  const ua = userAgent || "";
  const device = /iPhone/.test(ua)
    ? "iPhone"
    : /iPad/.test(ua)
      ? "iPad"
      : /Android/.test(ua)
        ? "Android"
        : /Macintosh/.test(ua)
          ? "Mac"
          : /Windows/.test(ua)
            ? "Windows"
            : "Device";
  if (standalone) return `${device} · Home Screen app`;
  const browser = /Edg\//.test(ua)
    ? "Edge"
    : /Firefox\//.test(ua)
      ? "Firefox"
      : /Chrome\//.test(ua) || /CriOS\//.test(ua)
        ? "Chrome"
        : /Safari\//.test(ua)
          ? "Safari"
          : "browser";
  return `${device} · ${browser}`;
}
