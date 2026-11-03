import type { NextConfig } from "next";

// In production Traefik routes /v1 to the API before requests reach this server; the
// rewrite only serves development and end-to-end tests.
const api = process.env.LIFEOS_API_URL ?? "http://127.0.0.1:8000";

const config: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  reactStrictMode: true,
  async rewrites() {
    return [{ source: "/v1/:path*", destination: `${api}/v1/:path*` }];
  },
  async headers() {
    return [
      {
        source: "/:path*",
        // No Content-Security-Policy here: src/proxy.ts sends it with a new nonce for every
        // response (policy and T14 limits in src/lib/csp.ts). A second, static policy would
        // apply on top of it and block the scripts.
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "no-referrer" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Permissions-Policy", value: "camera=(), geolocation=(), microphone=(self)" },
        ],
      },
    ];
  },
};

export default config;
