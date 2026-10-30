import { NextRequest, NextResponse } from "next/server";

export function proxy(request: NextRequest) {
  if (request.nextUrl.pathname.startsWith("/api/")) {
    const target = process.env.API_INTERNAL_URL;
    if (!target) return new NextResponse("API unavailable", { status: 503 });
    let destination: URL;
    try {
      destination = new URL(target);
    } catch {
      return new NextResponse("API unavailable", { status: 503 });
    }
    if (destination.protocol !== "http:" || !["api", "127.0.0.1"].includes(destination.hostname)) {
      return new NextResponse("API route configuration invalid", { status: 503 });
    }
    destination.pathname = request.nextUrl.pathname;
    destination.search = request.nextUrl.search;
    // Cloudflare Access adds this signed assertion at the edge. The API
    // validates its signature, issuer, audience and owner identity itself.
    // Next's rewrite must explicitly forward the incoming request headers;
    // otherwise the private API correctly returns 401 for every dashboard
    // request even though the browser passed Access.
    const apiHeaders = new Headers();
    for (const name of [
      "accept",
      "content-type",
      "origin",
      "cf-access-jwt-assertion",
      "x-requested-with",
    ]) {
      const value = request.headers.get(name);
      if (value !== null) apiHeaders.set(name, value);
    }
    return NextResponse.rewrite(destination, { request: { headers: apiHeaders } });
  }

  const nonce = Buffer.from(crypto.randomUUID()).toString("base64");
  const csp = [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'`,
    `style-src 'self' 'nonce-${nonce}'`,
    "style-src-attr 'unsafe-inline'",
    "img-src 'self' data:",
    "font-src 'self'",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
  ].join("; ");
  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("x-nonce", nonce);
  requestHeaders.set("Content-Security-Policy", csp);
  const response = NextResponse.next({ request: { headers: requestHeaders } });
  response.headers.set("Content-Security-Policy", csp);
  response.headers.set("X-Content-Type-Options", "nosniff");
  response.headers.set("Referrer-Policy", "no-referrer");
  response.headers.set("X-Frame-Options", "DENY");
  response.headers.set("Permissions-Policy", "camera=(), microphone=(), geolocation=()");
  response.headers.set("Cache-Control", "no-store");
  return response;
}

export const config = { matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"] };
