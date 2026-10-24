/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  reactStrictMode: true,
  transpilePackages: ["three"],
  // /api/* is proxied at request time by app/api/[...path]/route.ts (reads OMNIFIC_API_URL at runtime).
};
export default nextConfig;
