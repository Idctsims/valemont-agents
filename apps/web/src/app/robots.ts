import type { MetadataRoute } from "next";

// Private app: crawlers are kept out of everything except the public privacy
// policy (Pinterest's app review links to it). Pages also send
// X-Robots-Tag: noindex (next.config.ts) and a robots meta tag.
export default function robots(): MetadataRoute.Robots {
  return {
    rules: { userAgent: "*", allow: "/privacy", disallow: "/" },
  };
}
